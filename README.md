# PennyWise

> Agentic portfolio advisor for retail Indian investors on Groww.
> Talks to your live holdings, runs the risk math locally, and uses a
> Claude reasoning loop to suggest concrete Buy / Hold / Sell / Trim
> actions — every number backed by a tool call, not LLM training data.

```
$ uv run pennywise chat

you: am I over-concentrated?
pennywise: Yes — Financial Services is 34.5% of your stock book (HHI 0.21,
borderline "concentrated"). Your top holding, RECLTD, is 12.3%. Banks
alone are 22%. Trimming RECLTD to ~10% and adding one IT or Healthcare
name (both currently <3%) would bring HHI under 0.15.
```

## How auth works

**CLI (single user):** link your Groww API credentials once via
`pennywise login groww`. Everything is stored in
`~/.pennywise/credentials.json`; tokens auto-refresh at 6 AM IST daily.

**API / web (multi-user):** uses **Google OAuth** as the identity layer.
Sign in with Google → receive a PennyWise JWT → use it for all API
calls. Groww credentials are linked to your Google identity and stored
in DynamoDB.

## Why this exists

The Groww app shows what you own. It doesn't tell you whether you're
over-exposed to financials, whether your tech name's RSI is in oversold
territory, or which mid-cap candidate would best plug the gap between
your equity and the broad market. PennyWise does — and shows its work.

## Architecture

```mermaid
graph LR
    subgraph CLI ["CLI  (single user)"]
        A1[pennywise login groww] --> A2[credentials.json<br/>auto-refresh at 6 AM IST]
        A2 --> A3[snapshot · risk · recommend · chat]
    end

    subgraph API ["API  (multi-user · Docker)"]
        B1[Google OAuth] --> B2[DynamoDB<br/>users · sessions · jobs]
        B2 --> B3[FastAPI + JWT]
        B3 --> B4[WebSocket chat]
    end

    A3 & B3 --> C[Connectors<br/>Groww · Screener · yfinance · Moneycontrol]
    C --> D[Analytics<br/>HHI · sector · mcap · gaps]
    D --> E[LangGraph workflow]
    E --> F[Claude — Synthesizer + Critic<br/>extended thinking]
```

### Recommendation pipeline

```mermaid
graph TD
    A[Snapshot<br/>Groww holdings + Screener tags] --> B[Risk engine<br/>HHI · sector · mcap · gaps]
    B --> C[Candidate picker<br/>Nifty 500 universe]
    C --> D[Fundamentals<br/>Screener.in]
    D --> E[Technicals<br/>yfinance]
    E --> F[News<br/>Moneycontrol RSS]
    F --> G[Synthesizer<br/>Claude + extended thinking]
    G --> H[Critic<br/>Claude + extended thinking]
    H -- revise --> G
    H -- accept --> I[Final recommendations]

    style G fill:#7c5cff,color:#fff
    style H fill:#7c5cff,color:#fff
```

Three design choices worth calling out:

- **Two surfaces.** The CLI is single-user and only needs Groww credentials
  (`pennywise login groww`, run once). The API/Docker backend is multi-user
  and uses Google OAuth → PennyWise JWT for identity.

- **Reasoning models on the analytical nodes.** Synthesis and critique
  are the two places where Claude has to weigh many signals at once; we
  give them extended-thinking budgets so they can plan internally before
  emitting the final recommendation. Every other node is pure Python.

- **Snapshot persistence.** `pennywise snapshot` does the slow, network-
  heavy tagging once; downstream commands (`risk`, `recommend`, `chat`)
  read the on-disk snapshot and finish in seconds. Re-tagging is opt-in
  via `--fresh`.

## Quickstart

### CLI (single user, local)

```bash
git clone https://github.com/udit-amin/PennyWise.git
cd PennyWise
uv sync
echo "ANTHROPIC_API_KEY=sk-ant-…" >> .env

pennywise login groww      # link your Groww account (checksum or TOTP — see below)
pennywise snapshot         # fetch + tag your holdings (~30-60s the first time)
pennywise chat             # ask anything
```

### API + Docker (multi-user, with web frontend)

```bash
# Fill .env: GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, ANTHROPIC_API_KEY
# Add http://localhost:8000/api/auth/google/callback as an authorised
# redirect URI (Web app type) in Google Cloud Console.

docker-compose up          # API on :8000, DynamoDB-local on :8042

# In browser:
# → http://localhost:8000/login
# → Sign in with Google → PennyWise JWT shown on screen

# Link your Groww account (Authorization: Bearer <jwt>):
# POST /api/auth/groww-credentials  {"api_key": "…", "api_secret": "…"}
```

API docs auto-generate at `http://localhost:8000/docs`.

To run the API without Docker:

```bash
uv sync
uv run uvicorn pennywise.api.app:create_app --factory --reload
```

## CLI commands

| Command | What it does |
|---|---|
| `pennywise login groww` | Link your Groww account. Prompts for API credentials and stores them in `~/.pennywise/credentials.json`. Two auth methods — see below. |
| `pennywise snapshot` | Fetch Groww holdings + LTP, tag every ticker with sector / industry / market cap from Screener, persist to `~/.pennywise/snapshot.json`. |
| `pennywise risk` | Read snapshot, compute HHI / sector mix / market-cap mix / gaps, generate LLM commentary. |
| `pennywise recommend` | Run the full LangGraph workflow: candidate pick → fundamentals → technicals → news → synthesis → critique → finalize. |
| `pennywise chat` | Interactive REPL. Claude has tool access to your portfolio. |

### Groww auth methods

`pennywise login groww` asks you to choose between two methods:

| Method | Setup | Refresh |
|---|---|---|
| **Checksum** (recommended) | API Key + Secret from your Groww developer account | Fully automatic — run once |
| **TOTP** | API Key + base32 secret (the string shown next to the QR code on Groww's TOTP setup screen, e.g. `GJ4AHT26…`) | Fully automatic — PennyWise generates the 6-digit codes from the stored secret |

Both methods auto-refresh daily at 6:00 AM IST (when Groww rotates tokens). No daily re-login required for either.

### Session persistence

Every chat is autosaved to `~/.pennywise/chats/<id>.json` after every turn. `pennywise chat` resumes the most recent session by default.

In-REPL commands:

```
/help       list commands
/new        start a fresh session
/sessions   list saved sessions, newest first
/load <id>  resume a specific session
/where      path to the current session file
/verbose    toggle tool-call tracing
/quit       exit
```

### Chat tools

Claude is wired up with seven deterministic tools:

| Tool | Source | Speed |
|---|---|---|
| `get_holdings` | snapshot | instant |
| `get_risk_metrics` | snapshot | instant |
| `analyze_ticker(symbol)` | snapshot | instant |
| `fetch_technicals(symbol)` | yfinance (live) | ~2-4s |
| `fetch_fundamentals(symbol)` | Screener.in (live) | ~1-2s |
| `fetch_news(symbol)` | Moneycontrol RSS (live) | ~1-2s |
| `list_recommendations(focus)` | full LangGraph workflow | ~30s |

Live tools accept any NSE symbol — held or not. Questions like *"should I
buy INFY?"* trigger fundamentals + technicals fetches and a real,
signal-cited answer.

```bash
pennywise chat                 # resume most recent session
pennywise chat --new           # start fresh
pennywise chat --session <id>  # resume a specific session
pennywise chat --verbose       # trace every tool call
pennywise chat --no-reasoning  # skip extended thinking
```

## API endpoints

All endpoints require `Authorization: Bearer <pennywise-jwt>` (except `/health` and `/login`).

| Method | Path | Description |
|---|---|---|
| GET | `/login` | Sign-in page with Google button |
| GET | `/health` | Health check |
| GET | `/api/auth/google/start` | Redirect to Google OAuth |
| GET | `/api/auth/google/callback` | Browser OAuth callback → HTML page with JWT |
| POST | `/api/auth/google/callback` | JSON OAuth callback (for JS frontends) |
| GET | `/api/auth/me` | Current user info |
| POST | `/api/auth/groww-credentials` | Link Groww API credentials to account |
| GET | `/api/portfolio/holdings` | Holdings with sector + P&L |
| GET | `/api/portfolio/risk` | Concentration / risk metrics |
| GET | `/api/tools/technicals/{symbol}` | Live technical indicators |
| GET | `/api/tools/fundamentals/{symbol}` | Live fundamentals from Screener |
| GET | `/api/tools/news/{symbol}` | Recent Moneycontrol headlines |
| POST | `/api/recommendations` | Start recommendation workflow (async job) |
| GET | `/api/recommendations/{job_id}` | Poll job status |
| WebSocket | `/api/chat/ws?token=<jwt>` | Streaming chat with tool calls |

## MCP integration (optional)

Register PennyWise alongside Groww's official MCP server in
`~/.claude.json` so prompts inside any Claude Code session can invoke
the same tools:

```json
{
  "mcpServers": {
    "groww": {
      "command": "npx",
      "args": ["-y", "@groww/mcp"],
      "env": { "GROWW_API_TOKEN": "<bearer>" }
    },
    "pennywise": {
      "command": "uv",
      "args": ["run", "python", "-m", "pennywise.mcp.server"],
      "cwd": "/absolute/path/to/PennyWise"
    }
  }
}
```

## Configuration

All knobs live in `.env` (copy from `.env.example`):

| Var | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for `risk`, `recommend`, `chat`. |
| `GROWW_API_TOKEN` | — | Pre-minted daily Groww token (alternative to running `pennywise login groww`). |
| `PENNYWISE_LLM_MODEL` | `claude-opus-4-7` | Claude model id. Sonnet-class models also work and cost ~3× less. |
| `PENNYWISE_REASONING_EFFORT` | `medium` | Adaptive-thinking effort for Synthesizer / Critic (`low` / `medium` / `high`). |
| `PENNYWISE_HHI_FLAG` | `0.25` | HHI threshold for the "concentrated" flag. |
| `PENNYWISE_TOP_NAME_FLAG` | `0.20` | Single-name weight that triggers a TRIM suggestion. |
| `PENNYWISE_LARGE_CAP_FLOOR_CR` | `80000` | AMFI top-100 floor (H1 2025). |
| `PENNYWISE_MID_CAP_FLOOR_CR` | `28000` | AMFI top-250 floor (H1 2025). |
| `GOOGLE_CLIENT_ID` | — | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | — | Google OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/api/auth/google/callback` | Redirect URI registered in Google Cloud Console (API backend). For CLI, register `http://localhost:18765/callback` separately. |
| `JWT_SECRET` | `pennywise-dev-secret-change-me` | JWT signing secret for the API backend. Change this in production. |
| `DYNAMODB_ENDPOINT` | — | DynamoDB-local URL (`http://localhost:8042`); leave unset for real AWS. |
| `CORS_ORIGINS` | `localhost:3000,5173` | Comma-separated allowed origins for the API. |

Refresh the market-cap floors biannually from
[amfiindia.com → Categorization of Stocks][amfi].

[amfi]: https://www.amfiindia.com/research-information/other-data

## Tests

```bash
uv run pytest -q
```

73 tests, all offline — mocked HTTP responses and inline HTML fixtures,
no live calls.

## Project layout

```
pennywise/
├── cli.py                 # typer entrypoint
├── chat.py                # interactive REPL + tool definitions
├── config.py              # .env loading
├── credentials.py         # ~/.pennywise/credentials.json store + TOTP generation
├── login.py               # pennywise login groww / google flows
├── snapshot.py            # on-disk portfolio cache
├── tagging.py             # build_snapshot() — holdings + LTP + Screener tags
├── connectors/            # groww / screener / yfinance / moneycontrol
├── analytics/             # pure-python risk math, sector canonicalisation
├── agents/                # one node per workflow step
│   ├── _llm.py            # shared structured-output helper (with reasoning)
│   ├── strategy_synthesizer.py
│   ├── strategy_critic.py
│   └── …
├── graph/                 # LangGraph state + workflow wiring
├── api/                   # FastAPI backend
│   ├── app.py             # application factory + /login page
│   ├── auth.py            # Google OAuth + JWT
│   ├── db.py              # DynamoDB persistence
│   ├── streaming.py       # WebSocket chat adapter
│   ├── background.py      # thread-pool job runner
│   ├── models.py          # Pydantic request/response schemas
│   └── routes/            # auth, portfolio, tools, chat, recommendations
├── mcp/                   # FastMCP server exposing tools
└── data/
    └── universe.csv       # static Nifty-universe candidate pool
```

## Roadmap

- [ ] Live AMFI category lookup instead of threshold-based mcap bucketing.
- [ ] XIRR + dividend history (Groww publishes both via the portfolio API).
- [ ] Optional Streamlit dashboard for the chat surface.
- [ ] Per-user snapshot persistence in the API (currently uses the shared CLI snapshot).

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

PennyWise is a research prototype. It is **not** investment advice. Read
every recommendation critically before acting on it; the LLM can be
confidently wrong, which is exactly why every claim is wired back to a
tool call you can audit.
