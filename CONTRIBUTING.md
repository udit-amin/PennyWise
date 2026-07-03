# Contributing to PennyWise

Thanks for considering a contribution. PennyWise is a small project with
a few conventions worth knowing before opening a PR.

## Setup

```bash
git clone https://github.com/udit-amin/PennyWise.git
cd PennyWise
uv sync
cp .env.example .env  # only needed to run the app — the test suite needs no credentials at all
uv run pytest -q
```

## Architecture rules of thumb

- **Tests must not hit the network**, and don't need any credentials —
  the full suite passes with zero environment variables set. Two mocking
  patterns depending on layer:
  - **Connectors** (`pennywise/connectors/`): swap in an `httpx.MockTransport`
    that returns canned responses for the paths the client hits — see
    `tests/test_groww.py` for the pattern.
  - **API routes** (`pennywise/api/`): `tests/conftest.py`'s `FakeDB` is an
    in-memory stand-in monkeypatched onto `pennywise.api.db`, since every
    route reaches DynamoDB exclusively through that module's functions. No
    moto, no dynamodb-local, no AWS access needed. `app_client` gives you a
    `TestClient(create_app())`; `auth_headers` mints a real JWT for a seeded
    test user.
- **One concern per agent node.** If a node grows to do two things
  (e.g. fetch + transform), split it. The graph in
  `pennywise/graph/workflow.py` should read like a sentence.
- **Pure Python over LLM for math.** Numbers (HHI, weights, P&L) are
  computed in `analytics/`. The LLM only writes prose and picks actions.
- **Structured outputs only.** Every Claude call goes through
  `agents/_llm.py::structured_call`, which uses tool-use to guarantee
  parseable output. No regex on raw model text.
- **Per-user data flows through the `Snapshot`, never raw credentials.**
  Graph nodes and chat tools never see a Groww token — the API layer
  resolves a `Snapshot` (`pennywise/api/groww_creds.py::snapshot_provider`)
  and injects it before anything downstream runs. See
  `docs/ARCHITECTURE.md`'s "Multi-user portfolio resolution" section before
  touching credential or portfolio-fetch code.

## Coding style

- Type hints everywhere except trivial locals.
- Docstrings explain *why*, not *what* (the code already says what).
- `from __future__ import annotations` at the top of new modules.
- Imports sorted: stdlib, third-party, local — blank lines between.

## Adding a connector

1. Add the HTTP client under `pennywise/connectors/<name>.py`.
2. Write a test with an `httpx.MockTransport` returning inline canned
   responses for the paths/params the client hits (see
   `tests/test_groww.py`) — no fixture files, no live calls.
3. Assert the parsed shape the connector returns, not the raw response.
4. Wire it into an agent node only after the parser test is green.

## Adding a chat tool

1. Define a `tool_<name>(...)` pure function in `pennywise/chat.py`. If it
   needs portfolio data, accept an optional `get_snapshot` parameter
   instead of reading the module-level snapshot directly (see
   `tool_get_holdings` for the pattern) — this is what lets the same tool
   serve both the single-user CLI and the multi-user API.
2. Add a matching entry to `TOOL_SPECS` (JSONSchema for inputs) and wire
   the callable into `make_tool_impls()`. If the tool touches a portfolio,
   wrap it in `_portfolio_guard` so an unlinked user gets a graceful
   `groww_not_linked` result instead of an exception.
3. Add a test in `tests/test_chat.py` passing a fake `get_snapshot`
   callable rather than patching module state.
4. The system prompt in `chat.py::SYSTEM` already tells Claude to call a
   tool before stating numbers — no prompt edit needed unless the new
   tool is non-obvious to use.

## Adding an API route

1. Add the route in `pennywise/api/routes/<area>.py`; request/response
   shapes go in `pennywise/api/models.py` (Pydantic).
2. Any DynamoDB access goes through a function in `pennywise/api/db.py` —
   never call boto3 directly from a route. Since `db.py` is synchronous,
   call it from an async route via `await asyncio.to_thread(db.fn, ...)`.
3. If the route needs the caller's portfolio, take a
   `get_snapshot = groww_creds.snapshot_provider(user)` closure rather than
   fetching Groww data directly — see `routes/portfolio.py`.
4. Add a test in `tests/test_api_*.py` using the `app_client`/`fake_db`/
   `auth_headers` fixtures from `tests/conftest.py` — not real AWS.
5. Update the endpoint table in `README.md` if the route is user-facing.

## Refreshing AMFI market-cap floors

Twice a year (Jan / Jul) AMFI republishes the top-100 and top-250
cutoffs. Update the defaults in `pennywise/config.py` and the comment
in `.env.example` accordingly. The values for H1 2025 are 80,000 Cr /
28,000 Cr.

## Pull requests

- One logical change per PR.
- `uv run pytest -q` must pass.
- Update the README if user-visible behaviour changes.
- For LLM prompt changes, paste a before/after sample in the PR
  description so reviewers can sanity-check the new output shape.
