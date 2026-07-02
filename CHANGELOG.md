# Changelog

All notable changes to PennyWise are listed here. Entries are written as
user-facing impact; see `git log` for the full commit history.

---

## Unreleased

### Added
- **Production deployment to AWS ECS/Fargate.** Terraform under `infra/`
  provisions VPC, ECS Fargate service, ALB (HTTPS + WebSocket), DynamoDB
  (PITR + SSE), Secrets Manager, and CloudWatch alarms, with a Terraform
  workspace per environment (`staging`, `prod`); `dev` stays local.
- **CI/CD via GitHub Actions** — tests on every PR; build-once images promoted
  staging → prod, prod gated behind a manual approval Environment.
- **Multi-user portfolios.** Each API user connects their own Groww account
  (`POST /api/auth/groww-credentials`, verified against Groww and encrypted
  at rest) or uploads a broker holdings statement
  (`POST /api/portfolio/upload`, CSV/XLSX) as a low-friction alternative.
  `GET /api/auth/groww-credentials/status` reports linkage. Users without a
  portfolio get generic market-data chat instead of an error; portfolio
  endpoints return 409 with next steps.
- `/health/ready` readiness probe (checks DynamoDB) alongside the existing
  `/health` liveness probe.
- Per-user rate limiting on the recommendation and chat endpoints (shared
  across workers via a DynamoDB counter) to cap LLM spend.
- `PENNYWISE_ENV` setting; the API now refuses to start in staging/prod with a
  default/missing `JWT_SECRET`, missing Google OAuth credentials, or a missing
  `PENNYWISE_CRED_KEY` (encrypts per-user Groww credentials).
- JSON structured logging with request ids; background job lifecycle logging.
- OAuth CSRF protection (signed state parameter) and a `redirect_uri`
  allowlist.
- Background jobs survive restarts cleanly: heartbeats, a wall-clock timeout,
  and startup reconciliation of orphaned jobs (previously stuck "running"
  forever after any deploy).

### Changed
- Default LLM model is now `claude-opus-4-8`.
- Hardened container: multi-stage build, non-root user, no hot-reload in prod,
  `exec` in the entrypoint so `SIGTERM` reaches uvicorn for graceful shutdown.
- CLI no longer requires Google login — `pennywise login groww` is the only
  prerequisite for all CLI commands. Google OAuth is used only by the API/web
  backend.
- Anthropic API calls now share a connection-pooled client with retries and
  timeouts instead of building a fresh client per call; chat tool execution
  is individually timeout-bounded so a hung scraper can't stall a session.
- **Breaking (chat WebSocket protocol):** JWT auth moved from a `?token=`
  query parameter (recorded in ALB access logs) to a first-frame
  `{"type": "auth", "token": "..."}` message.

### Fixed
- DynamoDB calls no longer block the event loop — they run in worker threads.
- Risk calculations no longer crash on holdings with a missing live price
  (`ltp=None`), which uploaded statements and failed LTP lookups can produce.

---

## [0.3] — Auth & login

### Added
- `pennywise login groww` — interactive wizard; choose checksum (API Key +
  Secret) or TOTP (API Key + base32 secret). Credentials stored in
  `~/.pennywise/credentials.json`.
- **TOTP auto-refresh** — PennyWise stores the base32 TOTP secret and
  generates 6-digit codes automatically at token expiry. No daily re-login
  needed for either auth method.
- Groww token auto-refresh daily at 6 AM IST for both checksum and TOTP.
- `pennywise login google` (API backend only) — Google OAuth browser flow
  for the web frontend; not required for CLI use.

---

## [0.2] — API backend

### Added
- FastAPI backend (`pennywise/api/`) with Google OAuth, DynamoDB persistence,
  and Docker Compose setup (`docker-compose up`).
- WebSocket streaming chat endpoint (`/api/chat/ws`).
- Async background job runner for the recommendation workflow
  (`/api/recommendations`).
- MCP server (`python -m pennywise.mcp.server`) exposing `portfolio_snapshot`,
  `portfolio_risk`, `fundamentals`, and `recommend` tools for use inside
  Claude Code sessions.
- Auto-generated API docs at `http://localhost:8000/docs`.

---

## [0.1] — Initial release

### Added
- `pennywise snapshot` — fetch Groww holdings + LTP, tag every ticker with
  sector / industry / market cap via Screener.in, cache to
  `~/.pennywise/snapshot.json`.
- `pennywise risk` — HHI, sector mix, market-cap mix, concentration flags,
  LLM narrative commentary.
- `pennywise recommend` — full LangGraph workflow: risk analysis → candidate
  selection → fundamentals + technicals + news (parallel) → Claude Synthesizer
  → Claude Critic (with one revision loop) → final recommendations.
- `pennywise chat` — interactive REPL with tool access to the snapshot, risk
  engine, live fundamentals, technicals, news, and the full recommendation
  workflow.
- Session persistence: chats autosaved to `~/.pennywise/chats/<id>.json`;
  most recent session resumes automatically.
- 73 offline tests.
