"""Interactive chat interface for PennyWise.

This is the headline user surface: a REPL where the user can ask plain
questions about their portfolio ("am I over-concentrated in banks?",
"what should I trim?", "should I add an IT stock?") and Claude answers
by calling deterministic Python tools defined here.

Architecture:
    User question
        │
        ▼
    Claude (with tools + extended thinking)
        │
        ├── get_holdings()          ─┐
        ├── get_risk_metrics()       │  pure-Python, no LLM,
        ├── analyze_ticker(symbol)   │  reads from the snapshot
        ├── list_recommendations()  ─┘
        │
        ▼
    Tool result
        │
        ▼
    Claude composes natural-language answer

Why this design:
    * Determinism — the numbers in the answer come from the same risk
      engine the CLI uses, not from the LLM's training data.
    * Auditability — every claim is backed by a tool call the user can
      replay (``--verbose`` prints them).
    * Cheap — most questions hit only one or two tools and one LLM turn.
"""
from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from anthropic import Anthropic
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt

from pennywise.agents.risk_analyzer import risk_analyzer_node
from pennywise.config import load
from pennywise.snapshot import Snapshot
from pennywise.tagging import build_snapshot
from pennywise.utils.ttl_cache import TTLCache

SYSTEM = """You are PennyWise, an honest, plain-English portfolio advisor
for a retail Indian investor on Groww (NSE/BSE).

You have tools that read the user's portfolio AND fetch live market data:

Portfolio (from a cached snapshot, instant):
    - get_holdings: held tickers with sectors, weights, P&L
    - get_risk_metrics: HHI, sector / market-cap / asset weights, top name
    - analyze_ticker(symbol): per-symbol drill-down (snapshot only)

Live market data (slower, hits the network):
    - fetch_technicals(symbol): yfinance — last close, RSI(14), SMA50,
      SMA200, MACD, 30-day annualised vol. Works for ANY NSE ticker, held
      or not. ≈2-4 seconds.
    - fetch_fundamentals(symbol): Screener.in — PE, PB, ROE, D/E, market
      cap, sector, industry. ≈1-2 seconds. Rate-limited; reuse the cached
      result when in doubt.
    - fetch_news(symbol): recent Moneycontrol headlines mentioning the
      ticker. ≈1-2 seconds.

Workflow:
    - list_recommendations(focus): run the full agent workflow with
      synthesizer + critic. Slow (~30s). Only invoke when the user
      explicitly asks "what should I buy/sell".

How to behave:
    * Always call a tool before stating numbers — never invent them.
    * If asked about a NEW ticker (not in holdings), call fetch_fundamentals
      + fetch_technicals before recommending. State the actual numbers.
    * Combine signals: don't say "looks good" if RSI is 30 and PE is 80 —
      explain the tension and pick HOLD / WAIT until signals align.
    * Be specific: cite the actual % / ratio / ticker, not vague claims.
    * Indian context: AMFI categories, NSE/BSE, ₹ (rupees / crore).
    * If the user asks something the tools can't answer (tomorrow's price,
      tax calculations, broker mechanics), say so plainly.

Length: terse by default. Bullet lists when comparing multiple names,
short paragraphs otherwise. No emojis.
"""

TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "get_holdings",
        "description": (
            "Return the user's current holdings with sector, market cap, "
            "quantity, average price, last traded price, position value, "
            "and unrealised P&L percent."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_risk_metrics",
        "description": (
            "Return concentration / risk metrics for the portfolio: "
            "asset allocation, sector weights (stocks only), market-cap "
            "weights (stocks only), HHI, top holding, concentration flag."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "analyze_ticker",
        "description": (
            "Return everything we know about a single ticker — sector, "
            "industry, market cap, and (if held) position size + P&L."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE symbol, e.g. RELIANCE, HDFCBANK.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "fetch_technicals",
        "description": (
            "Live technical indicators from yfinance for ANY NSE ticker "
            "(held or not). Returns last close, RSI(14), SMA50, SMA200, "
            "MACD, MACD signal, and 30-day annualised volatility. ≈2-4 "
            "seconds. Use this whenever the user asks about momentum / "
            "entry timing / 'is now a good time'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "NSE symbol without exchange suffix, e.g. INFY.",
                },
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "fetch_fundamentals",
        "description": (
            "Live fundamentals from Screener.in for ANY NSE ticker. "
            "Returns PE, PB, ROE, debt-to-equity, market cap (Cr), broad "
            "sector, sector, industry. ≈1-2 seconds. Rate-limited — "
            "don't refetch the same ticker in one conversation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol, e.g. INFY."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "fetch_news",
        "description": (
            "Recent Moneycontrol headlines mentioning the ticker. Returns "
            "up to 5 items: title, link, published timestamp. Useful for "
            "checking whether a price move has a known catalyst."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "NSE symbol, e.g. INFY."},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "list_recommendations",
        "description": (
            "Run the full agent workflow and return Buy / Hold / Sell / "
            "Trim / Buy-new recommendations with rationale. Slow (≈30s "
            "first call). Only invoke when the user explicitly asks "
            "what to do."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "focus": {
                    "type": "string",
                    "enum": ["all", "gaps", "rebalance", "new"],
                    "description": "Recommendation scope.",
                },
            },
        },
    },
]


# ────────────────────────────── tool impls ──────────────────────────────


def _snapshot() -> Snapshot:
    """Reuse a cached snapshot if fresh; else build one.

    CLI-only default: reads ~/.pennywise + local Groww credentials. The API
    passes a per-user ``get_snapshot`` into :func:`make_tool_impls` instead.
    """
    snap = Snapshot.load_if_fresh(max_age_s=2 * 60 * 60)
    if snap is None:
        snap = build_snapshot()
        snap.save()
    return snap


def _risk_state(get_snapshot: Callable[[], Snapshot] | None = None) -> dict:
    """Holdings + risk metrics computed from the current snapshot."""
    snap = (get_snapshot or _snapshot)()
    state = {
        "holdings": list(snap.holdings),
        "fundamentals": {
            h["symbol"]: {
                "broad_sector": h.get("broad_sector"),
                "sector": h.get("sector"),
                "industry": h.get("industry_raw") or h.get("industry"),
                "market_cap_cr": h.get("market_cap_cr"),
            }
            for h in snap.holdings if h.get("symbol")
        },
    }
    return risk_analyzer_node(state) | {"holdings_raw": state["holdings"]}


def tool_get_holdings(get_snapshot: Callable[[], Snapshot] | None = None) -> dict:
    enriched = _risk_state(get_snapshot)
    rows = []
    for h in enriched["holdings"]:
        qty = float(h.get("quantity") or 0)
        avg = float(h.get("avg_price") or 0)
        ltp = h.get("ltp")
        value = qty * ltp if ltp is not None else None
        pnl_pct = (ltp / avg - 1) * 100 if (ltp and avg) else None
        rows.append({
            "symbol": h.get("symbol"),
            "sector": h.get("sector"),
            "industry": h.get("industry_raw") or h.get("industry"),
            "market_cap_cr": h.get("market_cap_cr"),
            "quantity": qty,
            "avg_price": avg,
            "ltp": ltp,
            "value": value,
            "pnl_pct": pnl_pct,
        })
    rows.sort(key=lambda r: r["value"] or 0, reverse=True)
    return {"count": len(rows), "holdings": rows}


def tool_get_risk_metrics(get_snapshot: Callable[[], Snapshot] | None = None) -> dict:
    enriched = _risk_state(get_snapshot)
    return {
        "risk_metrics": enriched.get("risk_metrics"),
        "gaps": enriched.get("gaps"),
    }


def tool_analyze_ticker(symbol: str, get_snapshot: Callable[[], Snapshot] | None = None) -> dict:
    sym = symbol.strip().upper()
    enriched = _risk_state(get_snapshot)
    match = next(
        (h for h in enriched["holdings"] if (h.get("symbol") or "").upper() == sym),
        None,
    )
    if match is None:
        return {"symbol": sym, "held": False, "note": "Ticker not in current portfolio."}
    qty = float(match.get("quantity") or 0)
    avg = float(match.get("avg_price") or 0)
    ltp = match.get("ltp")
    return {
        "symbol": sym,
        "held": True,
        "sector": match.get("sector"),
        "broad_sector": match.get("broad_sector"),
        "industry": match.get("industry_raw") or match.get("industry"),
        "market_cap_cr": match.get("market_cap_cr"),
        "quantity": qty,
        "avg_price": avg,
        "ltp": ltp,
        "value": qty * ltp if ltp else None,
        "pnl_pct": (ltp / avg - 1) * 100 if (ltp and avg) else None,
    }


def tool_list_recommendations(
    focus: str = "all",
    get_snapshot: Callable[[], Snapshot] | None = None,
) -> dict:
    # Imported lazily because run_pennywise pulls in yfinance + langgraph,
    # which is slow to import for chat sessions that never ask for recs.
    from pennywise.graph.workflow import run_pennywise

    if get_snapshot is not None:
        snap = get_snapshot()
        return run_pennywise(
            focus=focus,
            initial_holdings=snap.holdings,
            initial_positions=snap.positions,
        )
    return run_pennywise(focus=focus)


# ────────────────── live-data tools (cached per session) ──────────────────


# Per-process market-data caches (ticker-keyed, shared across users by
# design). Bounded + TTL'd so a long-running server neither grows without
# limit nor serves stale prices forever. Cleared by /new.
_TECHNICALS_CACHE = TTLCache(maxsize=512, ttl_s=15 * 60)
_FUNDAMENTALS_CACHE = TTLCache(maxsize=512, ttl_s=30 * 60)


def _norm_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def tool_fetch_technicals(symbol: str) -> dict:
    """Live yfinance pull. Cached for the lifetime of the chat process."""
    from dataclasses import asdict
    from pennywise.connectors.yfinance_client import YFinanceClient
    sym = _norm_symbol(symbol)
    if sym in _TECHNICALS_CACHE:
        return _TECHNICALS_CACHE[sym] | {"cached": True}
    try:
        tech = YFinanceClient().fetch(sym)
        result = asdict(tech)
    except Exception as exc:
        result = {"ticker": sym, "error": f"{type(exc).__name__}: {exc}"}
    _TECHNICALS_CACHE[sym] = result
    return result


def tool_fetch_fundamentals(symbol: str) -> dict:
    """Live Screener pull. Cached for the lifetime of the chat process."""
    from dataclasses import asdict
    from pennywise.connectors.screener import ScreenerScraper
    sym = _norm_symbol(symbol)
    if sym in _FUNDAMENTALS_CACHE:
        return _FUNDAMENTALS_CACHE[sym] | {"cached": True}
    try:
        with ScreenerScraper() as scr:
            fund = scr.fetch(sym)
        result = asdict(fund)
    except Exception as exc:
        result = {"ticker": sym, "error": f"{type(exc).__name__}: {exc}"}
    _FUNDAMENTALS_CACHE[sym] = result
    return result


def tool_fetch_news(symbol: str) -> dict:
    """Recent Moneycontrol headlines mentioning the symbol."""
    from pennywise.connectors.moneycontrol import MoneycontrolNews
    sym = _norm_symbol(symbol)
    try:
        with MoneycontrolNews() as mc:
            feed = mc.fetch()
            hits = mc.filter_for(feed, [sym])
        return {
            "symbol": sym,
            "count": len(hits),
            "items": [
                {
                    "title": n.title,
                    "link": n.link,
                    "published": n.published.isoformat() if n.published else None,
                }
                for n in hits[:5]
            ],
        }
    except Exception as exc:
        return {"symbol": sym, "error": f"{type(exc).__name__}: {exc}"}


def _clear_live_caches() -> None:
    """Wipe the per-session live-data caches (called by /new)."""
    _TECHNICALS_CACHE.clear()
    _FUNDAMENTALS_CACHE.clear()


_NOT_LINKED_RESULT = {
    "error": "groww_not_linked",
    "message": (
        "No portfolio available: the user hasn't connected a Groww account "
        "or uploaded a holdings statement. Portfolio tools are unavailable — "
        "explain this and offer market-data lookups instead."
    ),
}


def make_tool_impls(
    get_snapshot: Callable[[], Snapshot] | None = None,
) -> dict[str, Callable[[dict], dict]]:
    """Build the tool dispatch table.

    ``get_snapshot`` injects a per-user portfolio source (API path). Without
    it (CLI path) the local-snapshot default applies. Portfolio-dependent
    tools degrade to a structured ``groww_not_linked`` result when the source
    raises GrowwNotLinked, so chat keeps working without a portfolio.
    """

    def _portfolio_guard(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        def guarded(kw: dict) -> dict:
            from pennywise.api.groww_creds import GrowwNotLinked

            try:
                return fn(kw)
            except GrowwNotLinked:
                return dict(_NOT_LINKED_RESULT)

        return guarded

    return {
        "get_holdings": _portfolio_guard(lambda _kw: tool_get_holdings(get_snapshot)),
        "get_risk_metrics": _portfolio_guard(lambda _kw: tool_get_risk_metrics(get_snapshot)),
        "analyze_ticker": _portfolio_guard(
            lambda kw: tool_analyze_ticker(**kw, get_snapshot=get_snapshot)
        ),
        "fetch_technicals": lambda kw: tool_fetch_technicals(**kw),
        "fetch_fundamentals": lambda kw: tool_fetch_fundamentals(**kw),
        "fetch_news": lambda kw: tool_fetch_news(**kw),
        "list_recommendations": _portfolio_guard(
            lambda kw: tool_list_recommendations(**kw, get_snapshot=get_snapshot)
        ),
    }


# CLI default table — reads the local snapshot / credentials as before.
TOOL_IMPLS = make_tool_impls()


# ────────────────────────────── chat loop ──────────────────────────────


# ────────────────────────── session persistence ──────────────────────────


def chats_dir() -> Path:
    override = os.environ.get("PENNYWISE_CHATS_DIR")
    base = Path(override).expanduser() if override else Path.home() / ".pennywise" / "chats"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _serialize_history(history: list[dict]) -> list[dict]:
    """Convert Anthropic SDK content blocks into JSON-safe dicts.

    Assistant turns come back as SDK objects (TextBlock, ToolUseBlock,
    ThinkingBlock). We round-trip them through ``model_dump`` so the
    saved file is plain JSON. User turns are already plain dicts.
    """
    out: list[dict] = []
    for turn in history:
        content = turn.get("content")
        if isinstance(content, str):
            out.append({"role": turn["role"], "content": content})
            continue
        serialised = []
        for block in content:
            if isinstance(block, dict):
                serialised.append(block)
            elif hasattr(block, "model_dump"):
                serialised.append(block.model_dump(exclude_none=True))
            else:
                # Last-resort: best-effort attribute scrape
                serialised.append({
                    k: getattr(block, k) for k in dir(block)
                    if not k.startswith("_") and not callable(getattr(block, k, None))
                })
        out.append({"role": turn["role"], "content": serialised})
    return out


def list_sessions() -> list[dict]:
    """Return saved sessions, newest first."""
    out = []
    for p in sorted(chats_dir().glob("*.json"), reverse=True):
        try:
            data = json.loads(p.read_text())
            out.append({
                "id": p.stem,
                "path": str(p),
                "turns": len(data.get("history", [])),
                "started_at": data.get("started_at"),
                "last_user_message": data.get("last_user_message"),
            })
        except (OSError, json.JSONDecodeError):
            continue
    return out


def load_session_file(session_id: str) -> dict | None:
    path = chats_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def latest_session() -> dict | None:
    sessions = list_sessions()
    return sessions[0] if sessions else None


@dataclass
class ChatSession:
    client: Anthropic
    model: str
    history: list[dict]
    session_id: str
    verbose: bool = False
    reasoning: bool = True
    effort: str = "low"
    max_tool_iterations: int = 6

    # ── persistence ────────────────────────────────────────────────

    def save(self) -> Path:
        """Write the current history to disk after every turn. Atomic via
        a tmp-file rename so a crash mid-write never corrupts the session."""
        path = chats_dir() / f"{self.session_id}.json"
        tmp = path.with_suffix(".json.tmp")
        last_user = next(
            (t["content"] for t in reversed(self.history)
             if t.get("role") == "user" and isinstance(t.get("content"), str)),
            None,
        )
        payload = {
            "id": self.session_id,
            "model": self.model,
            "started_at": getattr(self, "_started_at", None) or datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "last_user_message": last_user,
            "history": _serialize_history(self.history),
        }
        self._started_at = payload["started_at"]
        tmp.write_text(json.dumps(payload, indent=2, default=str))
        tmp.replace(path)
        return path

    @classmethod
    def restore(cls, data: dict, *, client: Anthropic, model: str, **opts) -> "ChatSession":
        sess = cls(
            client=client,
            model=model,
            history=list(data.get("history", [])),
            session_id=data.get("id") or _new_session_id(),
            **opts,
        )
        sess._started_at = data.get("started_at")
        return sess

    # ── core loop ──────────────────────────────────────────────────

    def ask(self, user_text: str) -> str:
        self.history.append({"role": "user", "content": user_text})
        for _ in range(self.max_tool_iterations):
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": 4096,
                "system": SYSTEM,
                "tools": TOOL_SPECS,
                "messages": self.history,
            }
            if self.reasoning:
                # Adaptive thinking: the API decides scratchpad size from
                # the effort knob. "low" is plenty for chat — most answers
                # are one or two tool calls and a paragraph of prose.
                kwargs["thinking"] = {"type": "adaptive"}
                kwargs["output_config"] = {"effort": self.effort}
            msg = self.client.messages.create(**kwargs)

            # Always append the full assistant turn to history so subsequent
            # tool_result messages match the preceding tool_use ids.
            self.history.append({"role": "assistant", "content": msg.content})

            tool_uses = [b for b in msg.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:
                # No more tools to call — collect the text reply.
                texts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
                self.save()
                return "\n".join(texts).strip() or "(no response)"

            # Execute tools in parallel when Claude fires multiple in one turn.
            # This is common for "should I buy INFY?" → fetch_technicals +
            # fetch_fundamentals + fetch_news in parallel (~3-4s vs ~7-8s serial).
            tool_results = []
            if len(tool_uses) == 1:
                tu = tool_uses[0]
                if self.verbose:
                    print(f"  → tool: {tu.name}({dict(tu.input)})")
                impl = TOOL_IMPLS.get(tu.name)
                try:
                    result = impl(dict(tu.input)) if impl else {"error": f"unknown tool: {tu.name}"}
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__}: {exc}"}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })
            else:
                # Parallel execution for multi-tool turns.
                with ThreadPoolExecutor(max_workers=min(4, len(tool_uses))) as pool:
                    def _run_tool(tu_block):
                        if self.verbose:
                            print(f"  → tool: {tu_block.name}({dict(tu_block.input)})")
                        impl = TOOL_IMPLS.get(tu_block.name)
                        try:
                            return impl(dict(tu_block.input)) if impl else {"error": f"unknown tool: {tu_block.name}"}
                        except Exception as exc:
                            return {"error": f"{type(exc).__name__}: {exc}"}
                    futs = {pool.submit(_run_tool, tu): tu for tu in tool_uses}
                    for fut in as_completed(futs):
                        tu = futs[fut]
                        try:
                            result = fut.result()
                        except Exception as exc:
                            result = {"error": f"{type(exc).__name__}: {exc}"}
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tu.id,
                            "content": json.dumps(result, default=str),
                        })
            self.history.append({"role": "user", "content": tool_results})
            # Persist after the round-trip so a crash before the next API
            # call still leaves us replay-able.
            self.save()

        self.save()
        return "(stopped: hit tool-iteration cap)"


HELP_TEXT = (
    "[bold]/help[/bold]      show this message\n"
    "[bold]/new[/bold]       start a fresh session (saves the current one first)\n"
    "[bold]/sessions[/bold]  list saved sessions\n"
    "[bold]/load <id>[/bold] resume a saved session by id\n"
    "[bold]/where[/bold]     print the path to the current session file\n"
    "[bold]/verbose[/bold]   toggle tool-call tracing\n"
    "[bold]/quit[/bold]      exit chat (history is already saved)"
)


def run_chat(
    verbose: bool = False,
    no_reasoning: bool = False,
    resume: bool = True,
    session_id: str | None = None,
) -> None:
    """Launch an interactive PennyWise chat REPL.

    Args:
        resume: if True (default), auto-resume the most recent saved
            session. Pass ``--new`` on the CLI to start fresh.
        session_id: load this specific session id instead of the latest.
    """
    settings = load()
    if not settings.anthropic_api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to .env before running chat."
        )

    console = Console()
    client = Anthropic(api_key=settings.anthropic_api_key)

    # Decide which session to open.
    restored = None
    if session_id:
        restored = load_session_file(session_id)
        if restored is None:
            console.print(f"[yellow]No session {session_id!r} found — starting fresh.[/yellow]")
    elif resume:
        latest = latest_session()
        if latest:
            restored = load_session_file(latest["id"])

    if restored:
        session = ChatSession.restore(
            restored,
            client=client,
            model=settings.llm_model,
            verbose=verbose,
            reasoning=not no_reasoning,
            effort="low",
        )
        intro = (
            f"[bold cyan]PennyWise chat[/bold cyan] — resumed session "
            f"[bold]{session.session_id}[/bold] ({len(session.history)} turns).\n"
            f"Type [bold]/new[/bold] to start fresh, [bold]/help[/bold] for commands."
        )
    else:
        session = ChatSession(
            client=client,
            model=settings.llm_model,
            history=[],
            session_id=_new_session_id(),
            verbose=verbose,
            reasoning=not no_reasoning,
            effort="low",
        )
        intro = (
            "[bold cyan]PennyWise chat[/bold cyan] — ask anything about your "
            "Groww portfolio.\n"
            "Examples: [italic]'am I over-concentrated?'[/italic], "
            "[italic]'should I buy INFY?'[/italic], "
            "[italic]'analyse HDFCBANK'[/italic]\n"
            "Type [bold]/help[/bold] for commands, [bold]/quit[/bold] to exit."
        )
    console.print(Panel.fit(intro, border_style="cyan"))

    while True:
        try:
            user_text = Prompt.ask("\n[bold green]you[/bold green]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye — session saved.[/dim]")
            return
        if not user_text:
            continue
        if user_text in ("/quit", "/exit", "/q"):
            console.print("[dim]bye — session saved.[/dim]")
            return
        if user_text == "/help":
            console.print(HELP_TEXT)
            continue
        if user_text == "/new":
            session.save()  # flush the current one
            _clear_live_caches()
            session = ChatSession(
                client=client,
                model=settings.llm_model,
                history=[],
                session_id=_new_session_id(),
                verbose=verbose,
                reasoning=not no_reasoning,
                effort="low",
            )
            console.print(f"[dim]new session: {session.session_id}[/dim]")
            continue
        if user_text == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]no saved sessions yet.[/dim]")
            for s in sessions[:10]:
                first = (s.get("last_user_message") or "(no messages)")[:60]
                console.print(f"  [bold]{s['id']}[/bold]  {s['turns']:>3} turns  [dim]{first}[/dim]")
            continue
        if user_text.startswith("/load"):
            parts = user_text.split(maxsplit=1)
            if len(parts) < 2:
                console.print("[yellow]usage: /load <session-id>[/yellow]")
                continue
            data = load_session_file(parts[1].strip())
            if not data:
                console.print(f"[yellow]No session {parts[1]!r} found.[/yellow]")
                continue
            session.save()
            session = ChatSession.restore(
                data, client=client, model=settings.llm_model,
                verbose=verbose, reasoning=not no_reasoning, effort="low",
            )
            console.print(f"[dim]loaded {session.session_id} ({len(session.history)} turns).[/dim]")
            continue
        if user_text == "/where":
            console.print(f"[dim]{chats_dir() / (session.session_id + '.json')}[/dim]")
            continue
        if user_text == "/verbose":
            session.verbose = not session.verbose
            console.print(f"[dim]verbose = {session.verbose}[/dim]")
            continue

        with console.status("[dim]thinking…[/dim]"):
            try:
                answer = session.ask(user_text)
            except Exception as exc:
                console.print(f"[red]error:[/red] {exc}")
                continue
        console.print(Panel(Markdown(answer), title="pennywise", border_style="cyan"))
