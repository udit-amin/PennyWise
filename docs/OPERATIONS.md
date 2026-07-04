# Operations runbook

Day-to-day operation of the PennyWise API in staging/prod. For system design,
see [ARCHITECTURE.md](ARCHITECTURE.md); for provisioning a fresh AWS account
from nothing, see [AWS_SETUP.md](AWS_SETUP.md).

## Deploy

**Staging** deploys automatically on every merge to `main`
(`.github/workflows/deploy-staging.yml`): tests run, an image is built and
pushed to ECR tagged with the git SHA, and the staging ECS service rolls to
it.

**Prod** promotes an already-built, already-tested SHA — it never rebuilds
(`.github/workflows/deploy-prod.yml`, build-once-promote):

```bash
# Either tag the commit that's currently deployed on staging...
git tag v1.4.0 <sha> && git push origin v1.4.0

# ...or trigger manually with an explicit SHA:
gh workflow run deploy-prod.yml -f image_tag=<sha>
```

Both paths go through the `production` GitHub Environment's required-reviewer
gate — nothing reaches prod without a manual approval click in the Actions UI.

Every deploy runs `scripts/smoke.sh` against `API_URL` afterward (liveness,
readiness, login page, auth actually enforced, request-id header, and that
the Google OAuth redirect it builds carries a non-empty `redirect_uri` —
added after a real incident where it silently came out blank when no custom
domain was configured, which Google rejects outright). A failing smoke test
triggers an automatic rollback to the task definition that was running
before the deploy, and the workflow still fails — a bad release never sits
reported as "succeeded."

**What the automated smoke test deliberately does *not* cover** — these need
a live identity provider, a real third-party brokerage account, or spend real
Anthropic tokens, so they don't belong in a check that runs on every push:

- Completing a real Google OAuth consent (sign in, land on `/login` with a JWT)
- Linking a real Groww account (`POST /api/auth/groww-credentials`) or
  uploading a real holdings statement (`POST /api/portfolio/upload`)
- One real chat turn (`/api/chat/ws`) and one real recommendation run
  (`POST /api/recommendations`, polled to completion)

Run this manual pass once after standing up a new environment, and again
before promoting to prod if the auth/portfolio/chat code paths changed —
`README.md`'s "How to test it yourself" walkthrough has the exact steps
(browser login, `/docs` for REST endpoints, a short WebSocket snippet for
chat). This is a deliberate gap, not an oversight — write it down rather than
assume someone will remember to check it.

## Rollback

Two lines of defense, in order:

1. **Automatic.** The ECS deployment circuit breaker (`infra/ecs.tf`) reverts
   on its own if new tasks fail ALB health checks — no action needed. The
   deploy workflow's own smoke-test-triggered rollback (above) catches
   regressions that pass health checks but fail a real request.
2. **Manual**, via the `Rollback` workflow when something needs undoing after
   the fact (e.g. a bug found post-deploy that passed smoke tests):

   ```bash
   # Roll back to the immediately-prior task-definition revision:
   gh workflow run rollback.yml -f environment=production

   # Or to a specific, already-built image tag:
   gh workflow run rollback.yml -f environment=production -f image_tag=<sha>
   ```

   Prod rollback goes through the same required-reviewer approval as a
   normal prod deploy — it's still a production change.

## Secrets

Five Secrets Manager entries per environment (`pennywise-<env>-*`), injected
into the container as env vars by ECS — never baked into the image or task
definition in plaintext:

| Secret | Env var | Notes |
|---|---|---|
| `jwt-secret` | `JWT_SECRET` | Terraform-generated. **Rotating this invalidates every issued JWT** — all users are signed out. |
| `anthropic-api-key` | `ANTHROPIC_API_KEY` | From the Anthropic console. |
| `google-client-id` / `google-client-secret` | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console → APIs & Services → Credentials. Authorized redirect URI must match `GOOGLE_REDIRECT_URI` exactly. |
| `credentials-key` | `PENNYWISE_CRED_KEY` | Fernet key encrypting per-user Groww credentials at rest. **Rotating this orphans every already-linked Groww account** — users must re-link. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. |

Populate/rotate:

```bash
aws secretsmanager put-secret-value \
  --secret-id pennywise-staging-anthropic-api-key --secret-string 'sk-ant-…'
```

A task with a missing/empty secret fails `validate_auth_config()` or
`validate_crypto_config()` at boot and crash-loops — `_deploy.yml` checks all
five are populated before rolling, specifically to catch this before it
reaches a crash-loop.

## First-time table provisioning

Terraform is the source of truth for the DynamoDB schema
(`pennywise/api/db.py::_table_specs()`, mirrored in `infra/dynamodb.tf`) —
tables are created by `terraform apply`, not by the app. The one-shot step
after first provisioning an environment:

```bash
python -m pennywise.api.db --create   # AWS creds for the target env in the shell
```

(Local dev doesn't need this — `docker-compose up` auto-creates tables
against `dynamodb-local` on boot.)

## Alarms (`infra/observability.tf`)

All alarms notify the SNS topic subscribed to `var.alarm_email`.

| Alarm | Fires when | First response |
|---|---|---|
| `*-alb-5xx` | >5 target 5xx responses in 5 min | Check CloudWatch Logs (`/ecs/pennywise-<env>`) for the request ids around the spike; correlate with a recent deploy. |
| `*-unhealthy-hosts` | Any target unhealthy for 2 consecutive minutes | Check `/health/ready` — usually DynamoDB reachability or a crash-looping task (see Secrets above). |
| `*-dynamodb-throttle` | Any throttled DynamoDB request | Tables are `PAY_PER_REQUEST` so this means a burst beyond on-demand scaling, not a capacity misconfiguration — check for a runaway loop (e.g. a retry storm) rather than raising capacity. |
| `*-running-tasks-low` | Running task count < `desired_count` for 2 min | The task is crash-looping or was OOM-killed — check logs; `treat_missing_data = "breaching"` so total outage also pages. |

Not covered by an alarm (deliberately, per `infra/observability.tf`'s own
comment): NAT/data-transfer spend — watch via AWS Budgets at the account
level, set up once outside Terraform.

## Common incidents

- **Task crash-loops right after a deploy.** Almost always
  `validate_auth_config()` or `validate_crypto_config()` raising at boot —
  read the first few log lines in CloudWatch (`/ecs/pennywise-<env>`), the
  `RuntimeError` names exactly which secret is missing/default. Fix: populate
  the secret, no redeploy needed (ECS retries the task).
- **`/health/ready` returns 503.** `db.ping()` failed — check DynamoDB is
  reachable from the private subnet (VPC endpoint healthy) and the task role
  has `dynamodb:DescribeTable` (it does by default; only breaks if
  `infra/iam.tf` was hand-edited).
- **Anthropic outage / degraded.** LLM calls retry (`PENNYWISE_LLM_MAX_RETRIES`,
  default 3) then hard-timeout (`PENNYWISE_LLM_TIMEOUT_S`, default 120s); a
  chat turn caps at `PENNYWISE_CHAT_TURN_TIMEOUT_S` (default 300s) and
  recommendation jobs at `PENNYWISE_JOB_TIMEOUT_S` (default 600s) so a
  provider outage produces clean user-facing failures instead of hung
  connections — no manual intervention needed, but expect a spike in failed
  jobs/turns during the outage window.
- **Screener 429 storms.** The scraper backs off and eventually surfaces a
  `fundamentals_error` on affected holdings rather than failing the whole
  workflow; if sustained, recommendation quality degrades gracefully (missing
  fundamentals for some tickers) rather than erroring entirely.
- **A background job is stuck "running."** Shouldn't happen anymore —
  `reconcile_stale_jobs()` runs at boot and fails any job whose heartbeat
  went silent (crash/redeploy), and `PENNYWISE_JOB_TIMEOUT_S` bounds runaway
  jobs. If you see one anyway, check whether reconciliation itself is failing
  (it logs a warning but never blocks boot) — likely a DynamoDB permissions
  issue on the `jobs` table.

## Local development

```bash
docker-compose up          # API + dynamodb-local
uv run pytest -q           # 167 tests, no AWS/network needed — DynamoDB is monkeypatched
scripts/smoke.sh http://localhost:8000
```

`PENNYWISE_ENV` defaults to `dev`, which relaxes the fail-closed checks
(default JWT secret and a derived credential-encryption key are accepted) so
`docker-compose up` works with zero secret configuration.
