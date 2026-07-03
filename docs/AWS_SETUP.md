# AWS account setup

One-time walkthrough for standing up a **brand-new** AWS account for
PennyWise, security-first: no root access keys, no IAM users with static
credentials, least-privilege from the start. Do these in order — each step
depends on the one before it.

If the account already has these pieces in place, skip to whichever step is
still missing.

## 1. Root account hardening

Do this in the AWS Console, signed in as the root user, once:

1. **Enable MFA on the root user** (IAM → Root user → Security credentials).
2. **Confirm no root access keys exist.** If any do, delete them — the root
   user should never hold long-lived credentials.
3. Set **alternate security and billing contacts** (Account → Account
   settings) so account-level alerts don't depend on one person.
4. **Block S3 public access at the account level**
   (S3 → Block Public Access settings for this account → enable all four).
5. **Enable default EBS encryption** for the region you'll deploy to
   (EC2 → Account attributes → EBS encryption) — harmless even though this
   project doesn't provision EC2/EBS directly.
6. **Create an AWS Budget** (Billing → Budgets) with email alerts at 50/80/100%
   of an expected monthly spend. This is what catches a runaway NAT gateway
   or an accidentally-oversized Fargate task before it becomes a surprise bill.

## 2. Human access via IAM Identity Center (SSO)

Do not create an IAM user with an access key for yourself. Use IAM Identity
Center instead:

1. IAM Identity Center → Enable (choose the "organization" or "account-only"
   instance, whichever your setup allows).
2. Create a user for yourself.
3. Create a permission set — `AdministratorAccess` is fine for now (a single
   operator standing up the whole stack); tighten later if more people join.
4. Assign yourself that permission set on this account.
5. Configure the AWS CLI to use it:
   ```bash
   aws configure sso
   # SSO start URL and region come from the IAM Identity Center console
   ```
   Every command below assumes you're authenticated this way
   (`aws sso login` when the session expires).

## 3. State bucket bootstrap

Every Terraform config in this repo (`infra/versions.tf`,
`infra/shared/main.tf`) assumes an S3 bucket named `pennywise-tfstate`
already exists. Create it:

```bash
scripts/bootstrap-aws.sh
```

Idempotent — creates the bucket if missing, and always re-applies versioning,
encryption, public-access blocking, and a TLS-only policy. Safe to re-run.

## 4. Shared account-global resources

ECR repository, the GitHub OIDC deploy role, and the CloudTrail audit trail —
provisioned once, not per-environment:

```bash
cd infra/shared
terraform init
terraform apply
```

Note the `deploy_role_arn` output.

## 5. Wire up GitHub

1. **Repo secret**: Settings → Secrets and variables → Actions → New repository
   secret → `AWS_DEPLOY_ROLE_ARN` = the `deploy_role_arn` output from step 4.
2. **Repo variables** (same page, "Variables" tab) — one set per environment,
   read by `.github/workflows/_deploy.yml`:
   `AWS_REGION`, `ECR_REPOSITORY`, `ECS_CLUSTER`, `ECS_SERVICE`,
   `ECS_TASK_FAMILY`, `API_URL`. `ECS_CLUSTER`/`ECS_SERVICE`/`ECS_TASK_FAMILY`
   are all `pennywise-staging` or `pennywise-prod` (see
   `infra/locals.tf::name`); `ECR_REPOSITORY` is the `ecr_repository_url`
   output from step 4; `API_URL` is the `api_url` output from step 6 below
   (comes after the first per-env apply — circular only on the very first
   run, fill it in once you have it).
3. **`production` Environment**: Settings → Environments → New environment →
   `production` → add yourself (or your team) as a required reviewer. This is
   what makes `deploy-prod.yml` and `rollback.yml` (when targeting prod) wait
   for a manual approval click before touching the live service.
4. Also create a **`staging` Environment** (no required reviewers needed —
   staging deploys automatically on merge to `main`).

## 6. Google OAuth client

1. [Google Cloud Console](https://console.cloud.google.com/) → APIs &
   Services → Credentials → Create Credentials → OAuth client ID → Web
   application.
2. Authorized redirect URI: `https://<your-domain>/api/auth/google/callback`
   (must match `GOOGLE_REDIRECT_URI` / the `domain_name` tfvar exactly — see
   `pennywise/config.py::_allowed_redirect_uris`, which in staging/prod only
   accepts this one exact URI).
3. Save the client ID and secret — they go into Secrets Manager in step 8.

## 7. Provision an environment (staging first)

```bash
cd infra
terraform init
terraform workspace new staging
terraform apply -var-file=env/staging.tfvars
```

Fill in `domain_name` / `route53_zone_id` in `infra/env/staging.tfvars` first
if you have a hosted zone; without them the ALB serves plain HTTP off its own
DNS name, fine for an initial bring-up.

## 8. Populate secrets

```bash
aws secretsmanager put-secret-value --secret-id pennywise-staging-anthropic-api-key \
  --secret-string 'sk-ant-…'
aws secretsmanager put-secret-value --secret-id pennywise-staging-google-client-id \
  --secret-string '…'
aws secretsmanager put-secret-value --secret-id pennywise-staging-google-client-secret \
  --secret-string '…'
aws secretsmanager put-secret-value --secret-id pennywise-staging-credentials-key \
  --secret-string "$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
# pennywise-staging-jwt-secret is generated by Terraform automatically — no action needed.
```

(`terraform output secret_names` prints the exact secret names for the
current workspace.)

## 9. Create the DynamoDB tables

Terraform (step 7) already created them — this step is only needed if you
ever need to force-recreate/verify against a live environment:

```bash
PENNYWISE_ENV=staging PENNYWISE_TABLE_PREFIX=pennywise_staging_ AWS_REGION=ap-south-1 \
  python -m pennywise.api.db --create
```

## 10. First deploy

```bash
git push origin main   # deploy-staging.yml builds, pushes, and rolls automatically
```

Watch the Actions tab. Once it's green:

```bash
scripts/smoke.sh "$(cd infra && terraform output -raw api_url)"
```

## 11. Promote to prod

Repeat steps 7–9 with `terraform workspace new prod` and
`env/prod.tfvars`, then:

```bash
git tag v1.0.0 <sha-that-passed-staging>
git push origin v1.0.0   # deploy-prod.yml — waits for the `production` reviewer approval
```

---

From here on, day-to-day deploys/rollbacks/incident response are covered in
[OPERATIONS.md](OPERATIONS.md).
