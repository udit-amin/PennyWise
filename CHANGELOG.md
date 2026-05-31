# Changelog

All notable changes to PennyWise are listed here. Entries are written as
user-facing impact; see `git log` for the full commit history.

---

## Unreleased

### Changed
- CLI no longer requires Google login — `pennywise login groww` is the only
  prerequisite for all CLI commands. Google OAuth is used only by the API/web
  backend.

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
