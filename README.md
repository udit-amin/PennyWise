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

## Why this exists

The Groww app shows what you own. It doesn't tell you whether you're
over-exposed to financials, whether your tech name's RSI is in oversold
territory, or which mid-cap candidate would best plug the gap between
your equity and the broad market. PennyWise does — and shows its work.

## Architecture

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

Two design choices worth calling out:

- **Reasoning models on the analytical nodes.** Synthesis and critique
  are the two places where Claude has to weigh many signals at once; we
  give them extended-thinking budgets so they can plan internally before
  emitting the final recommendation. Every other node is pure Python.

- **Snapshot persistence.** `pennywise snapshot` does the slow, network-
  heavy tagging once; downstream commands (`risk`, `recommend`, `chat`)
  read the on-disk snapshot and finish in seconds. Re-tagging is opt-in
  via `--fresh`.

## Quickstart

```bash
git clone https://github.com/<you>/PennyWise.git
cd PennyWise
uv sync
cp .env.example .env       # then fill in GROWW_API_TOKEN + ANTHROPIC_API_KEY
uv run pennywise snapshot  # ~30-60s the first time
uv run pennywise chat      # ask anything
```

## Commands

| Command | What it does | Network |
|---|---|---|
| `pennywise snapshot` | Fetch Groww holdings + LTP, tag every ticker with sector / industry / market cap from Screener, persist to `~/.pennywise/snapshot.json`. | Groww + Screener |
| `pennywise risk` | Read snapshot, compute HHI / sector mix / market-cap mix / gaps, generate LLM commentary. | Anthropic only |
| `pennywise recommend` | Run the full LangGraph workflow: candidate pick → fundamentals → technicals → news → synthesis → critique → finalize. | All sources |
| `pennywise chat` | Interactive REPL. Claude has tool access to your portfolio. | Anthropic + on-demand |

## Chat interface

`pennywise chat` is the headline UX. Claude is wired up with seven
deterministic tools — three read the cached portfolio, three pull live
market data, one runs the full workflow:

| Tool | Source | Speed |
|---|---|---|
| `get_holdings` | snapshot | instant |
| `get_risk_metrics` | snapshot | instant |
| `analyze_ticker(symbol)` | snapshot | instant |
| `fetch_technicals(symbol)` | yfinance (live) | ~2-4s |
| `fetch_fundamentals(symbol)` | Screener.in (live) | ~1-2s |
| `fetch_news(symbol)` | Moneycontrol RSS (live) | ~1-2s |
| `list_recommendations(focus)` | full LangGraph workflow | ~30s |

Live tools accept ANY NSE symbol — held or not — so questions like
*"should I buy INFY?"* trigger fundamentals + technicals fetches and a
real, signal-cited answer.

```bash
uv run pennywise chat                 # resume the most recent session
uv run pennywise chat --new           # start fresh instead
uv run pennywise chat --session <id>  # resume a specific session
uv run pennywise chat --verbose       # trace every tool call
uv run pennywise chat --no-reasoning  # skip extended thinking
```

### Session persistence

Every chat is autosaved to `~/.pennywise/chats/<id>.json` after every
turn (atomic write — a crash mid-conversation never corrupts the file).

By default `pennywise chat` resumes the most recent session, so you can
exit, come back tomorrow, and continue where you left off — Claude
still has the conversation context.

In-REPL commands:

```
/help       list commands
/new        start a fresh session (saves the current one first)
/sessions   list saved sessions, newest first
/load <id>  resume a specific session
/where      print the path to the current session file
/verbose    toggle tool-call tracing
/quit       exit (already saved)
```

Example session:

```
you: what's my biggest risk?
pennywise: Concentration in PSUs. RECLTD (12.3%), PFC (8.1%), and
BANKBARODA (6.4%) together make up 26.8% — all rate-sensitive PSU
financials. If RBI cuts more slowly than the market expects, these
re-rate together.

you: should I trim RECLTD?
pennywise: Yes. It's your top holding at 12.3% (above the 10% top-name
flag), RSI is 35.4 (oversold but not extreme), price is below SMA50 and
SMA200, and MACD has crossed negative. Trim to 8-9% and redeploy into
the IT gap (TANLA or LTIM look strongest among the candidates).

you: /quit
```

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

Then `claude /mcp` will show both servers; ask *"What sector am I
over-exposed to?"* and the chat client will call
`pennywise.portfolio_risk` directly.

## Configuration

All knobs live in `.env`:

| Var | Default | Meaning |
|---|---|---|
| `GROWW_API_TOKEN` | — | Daily access token from Groww dashboard. |
| `GROWW_API_KEY` / `GROWW_API_SECRET` | — | Alternative: PennyWise mints the daily token from these. |
| `ANTHROPIC_API_KEY` | — | Required for `risk`, `recommend`, `chat`. |
| `PENNYWISE_LLM_MODEL` | `claude-opus-4-7` | Claude model id. Sonnet-class models also work and cost ~3× less. |
| `PENNYWISE_HHI_FLAG` | `0.25` | HHI threshold for the "concentrated" flag. |
| `PENNYWISE_TOP_NAME_FLAG` | `0.20` | Single-name weight that triggers a TRIM suggestion. |
| `PENNYWISE_LARGE_CAP_FLOOR_CR` | `80000` | AMFI top-100 floor (H1 2025). |
| `PENNYWISE_MID_CAP_FLOOR_CR` | `28000` | AMFI top-250 floor (H1 2025). |

Refresh the market-cap floors biannually from
[amfiindia.com → Categorization of Stocks][amfi].

[amfi]: https://www.amfiindia.com/research-information/other-data

## Tests

```bash
uv run pytest -q
```

50 tests, all offline — recorded HTTP fixtures, no live calls. Adding a
new connector? Add a fixture under `tests/fixtures/` and a test that
asserts the parser handles real responses.

## Project layout

```
pennywise/
├── cli.py                 # typer entrypoint
├── chat.py                # interactive REPL + tool definitions
├── config.py              # .env loading
├── snapshot.py            # on-disk portfolio cache
├── tagging.py             # build_snapshot() — holdings + LTP + Screener tags
├── connectors/            # groww / screener / yfinance / moneycontrol
├── analytics/             # pure-python risk math, sector canonicalisation
├── agents/                # one node per workflow step
│   ├── _llm.py            # shared structured-output helper (with reasoning)
│   ├── strategy_synthesizer.py
│   ├── strategy_critic.py
│   └── ...
├── graph/                 # LangGraph state + workflow wiring
├── mcp/                   # FastMCP server exposing tools
└── data/
    └── universe.csv       # static Nifty-universe candidate pool
```

## Roadmap

- [ ] Live AMFI category lookup instead of threshold-based mcap bucketing.
- [ ] XIRR + dividend history (Groww publishes both via the portfolio API).
- [ ] Save chat transcripts to `~/.pennywise/chats/` for replay.
- [ ] Optional Streamlit dashboard for the chat surface.

## License

MIT — see [LICENSE](LICENSE).

## Disclaimer

PennyWise is a research prototype. It is **not** investment advice. Read
every recommendation critically before acting on it; the LLM can be
confidently wrong, which is exactly why every claim is wired back to a
tool call you can audit.
