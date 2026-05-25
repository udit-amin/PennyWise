from __future__ import annotations

import typer
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich.table import Table

from pennywise.agents.risk_analyzer import risk_analyzer_node
from pennywise.agents.risk_commentator import risk_commentator_node
from pennywise.graph.workflow import run_pennywise
from pennywise.snapshot import Snapshot, snapshot_path
from pennywise.tagging import build_snapshot

app = typer.Typer(help="PennyWise — agentic stock advice for Groww portfolios.")
login_app = typer.Typer(help="Authenticate with Groww or Google.")
app.add_typer(login_app, name="login")
console = Console()


def _require_groww() -> None:
    """Exit with a clear message if Groww account is not linked."""
    from pennywise.credentials import is_logged_in_groww
    if not is_logged_in_groww():
        console.print(
            "\n[red]Groww account not linked.[/red]\n"
            "Run:  [bold]pennywise login groww[/bold]\n"
        )
        raise SystemExit(1)


@app.command()
def snapshot() -> None:
    """Fetch holdings + LTP + sector/industry tags from Groww and Screener,
    persist to ~/.pennywise/snapshot.json, and render the tagged portfolio."""
    _require_groww()
    with Progress(
        SpinnerColumn(),
        TextColumn("[bold]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        TextColumn("[dim]{task.fields[current]}"),
        console=console,
        transient=True,
    ) as prog:
        task = prog.add_task("Tagging holdings", total=None, current="")

        def on_step(i: int, total: int, sym: str | None) -> None:
            if prog.tasks[task].total != total:
                prog.update(task, total=total)
            prog.update(task, completed=i, current=sym or "")

        snap = build_snapshot(progress=on_step)

    path = snap.save()
    _render_holdings(snap)
    console.print(f"\n[dim]Saved snapshot → {path}[/dim]")


@app.command()
def risk(
    fresh: bool = typer.Option(False, "--fresh", help="Force a new snapshot before analysing."),
    max_age_min: int = typer.Option(120, "--max-age-min", help="Reject snapshots older than this."),
) -> None:
    """Analyse the on-disk snapshot: industry tagging is already done, so this
    is pure concentration math + LLM narrative commentary (no external HTTP)."""
    _require_groww()
    snap = _ensure_snapshot(fresh=fresh, max_age_s=max_age_min * 60)
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
    enriched = risk_analyzer_node(state)
    state.update(enriched)
    commentary = risk_commentator_node(state).get("risk_commentary", {})

    _render_risk(enriched["risk_metrics"], commentary)


@app.command()
def recommend(focus: str = typer.Option("all", help="all | gaps | rebalance | new")) -> None:
    """Run the full LangGraph workflow and print recommendations."""
    _require_groww()
    result = run_pennywise(focus=focus)
    _render_recommendations(result)


@app.command()
def chat(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Trace every tool call."),
    no_reasoning: bool = typer.Option(False, "--no-reasoning", help="Disable extended thinking."),
    new: bool = typer.Option(False, "--new", help="Start a fresh session (don't auto-resume)."),
    session: str | None = typer.Option(None, "--session", help="Resume a specific session id."),
) -> None:
    """Interactive REPL: ask Claude questions about your portfolio.

    By default resumes the most recent session from ``~/.pennywise/chats/``.
    Claude has tool access to the snapshot, the risk engine, live
    fundamentals (Screener) + technicals (yfinance) + news (Moneycontrol),
    and the full recommendation workflow.
    """
    _require_groww()
    # Imported lazily — chat pulls in the Anthropic SDK + rich.markdown,
    # neither of which `snapshot` or `risk` need.
    from pennywise.chat import run_chat
    run_chat(
        verbose=verbose,
        no_reasoning=no_reasoning,
        resume=not new,
        session_id=session,
    )


# ────────────────────────────── helpers ──────────────────────────────


def _ensure_snapshot(*, fresh: bool, max_age_s: float) -> Snapshot:
    if not fresh:
        existing = Snapshot.load_if_fresh(max_age_s=max_age_s)
        if existing is not None:
            age_min = existing.age_seconds() / 60
            console.print(f"[dim]Using snapshot from {age_min:.0f} min ago ({snapshot_path()})[/dim]")
            return existing
        if snapshot_path().exists():
            console.print(
                "[yellow]Snapshot is stale (>"
                f"{max_age_s / 60:.0f} min). Re-fetching…[/yellow]"
            )
        else:
            console.print("[yellow]No snapshot found. Fetching for the first time…[/yellow]")
    with Progress(SpinnerColumn(), TextColumn("[bold]{task.description}"),
                  BarColumn(), TaskProgressColumn(),
                  TextColumn("[dim]{task.fields[current]}"),
                  console=console, transient=True) as prog:
        task = prog.add_task("Tagging holdings", total=None, current="")

        def on_step(i: int, total: int, sym: str | None) -> None:
            if prog.tasks[task].total != total:
                prog.update(task, total=total)
            prog.update(task, completed=i, current=sym or "")
        snap = build_snapshot(progress=on_step)
    snap.save()
    return snap


def _render_holdings(snap: Snapshot) -> None:
    table = Table(title=f"Holdings (snapshot {snap.fetched_at})")
    for col, align in (("symbol", "left"), ("sector", "left"), ("industry", "left"),
                       ("qty", "right"), ("avg", "right"), ("ltp", "right"),
                       ("value", "right"), ("pnl %", "right")):
        table.add_column(col, justify=align)

    total_cost = total_value = 0.0
    for h in snap.holdings:
        qty = float(h.get("quantity") or 0)
        avg = float(h.get("avg_price") or 0)
        ltp = h.get("ltp")
        value = qty * ltp if ltp is not None else None
        pnl_pct = (ltp / avg - 1) * 100 if (ltp and avg) else None
        if value is not None:
            total_value += value
            total_cost += qty * avg
        table.add_row(
            h.get("symbol", "?"),
            h.get("sector") or "—",
            (h.get("industry_raw") or h.get("industry") or "—")[:32],
            f"{qty:g}",
            f"{avg:,.2f}" if avg else "",
            f"{ltp:,.2f}" if ltp is not None else "—",
            f"{value:,.0f}" if value is not None else "—",
            f"{pnl_pct:+.1f}%" if pnl_pct is not None else "—",
        )
    console.print(table)
    if total_cost:
        console.print(
            f"\n[bold]Total value:[/bold] ₹{total_value:,.0f}    "
            f"[bold]Cost:[/bold] ₹{total_cost:,.0f}    "
            f"[bold]Unrealised P&L:[/bold] "
            f"{(total_value - total_cost) / total_cost * 100:+.2f}%"
        )


def _render_risk(risk_m: dict, commentary: dict) -> None:
    total = risk_m.get("total_value", 0.0)
    stock_val = risk_m.get("stock_value", 0.0)

    aa = Table(title="Asset allocation")
    aa.add_column("class"); aa.add_column("weight", justify="right"); aa.add_column("value", justify="right")
    for cls, w in sorted(risk_m.get("asset_allocation", {}).items(), key=lambda kv: -kv[1]):
        aa.add_row(cls, f"{w * 100:5.1f}%", f"₹{w * total:,.0f}")
    console.print(aa)

    table = Table(title="Sector exposure (stocks only)")
    table.add_column("sector"); table.add_column("weight", justify="right"); table.add_column("value", justify="right")
    for sector, w in sorted(risk_m.get("sector_weights", {}).items(), key=lambda kv: -kv[1]):
        table.add_row(sector, f"{w * 100:5.1f}%", f"₹{w * stock_val:,.0f}")
    console.print(table)

    mcap = Table(title="Market-cap mix (stocks only)")
    mcap.add_column("bucket"); mcap.add_column("weight", justify="right"); mcap.add_column("value", justify="right")
    for b, w in sorted(risk_m.get("market_cap_weights", {}).items(), key=lambda kv: -kv[1]):
        mcap.add_row(b, f"{w * 100:5.1f}%", f"₹{w * stock_val:,.0f}")
    console.print(mcap)

    top = risk_m.get("top_holding") or {}
    console.print(
        f"\n[bold]HHI:[/bold] {risk_m.get('hhi_sector', 0):.3f}    "
        f"[bold]Top name:[/bold] {top.get('symbol')} ({(top.get('weight') or 0) * 100:.1f}%)    "
        f"[bold]Concentrated:[/bold] {risk_m.get('concentration_flag')}"
    )

    if commentary:
        console.print("\n[bold cyan]Commentary[/bold cyan]")
        console.print(f"[bold]{commentary.get('headline', '')}[/bold]")
        for c in commentary.get("concerns", []):
            sev = c.get("severity", "?").upper()
            console.print(f"  • [{sev}] {c.get('issue')}: {c.get('detail')}")
        actions = commentary.get("suggested_actions") or []
        if actions:
            console.print("\n[bold]Suggested actions[/bold]")
            for a in actions:
                console.print(f"  → {a}")


def _render_recommendations(result: dict) -> None:
    """Pretty-print the workflow output instead of dumping JSON."""
    summary = result.get("summary") or ""
    if summary:
        console.print(f"\n[bold cyan]{summary}[/bold cyan]\n")

    recs = result.get("recommendations") or []
    if not recs:
        console.print("[yellow]No recommendations returned.[/yellow]")
    else:
        action_style = {
            "BUY_NEW": "bold green",
            "ADD":     "green",
            "HOLD":    "white",
            "TRIM":    "yellow",
            "SELL":    "bold red",
        }
        table = Table(title="Recommendations")
        table.add_column("ticker"); table.add_column("action")
        table.add_column("conf", justify="right")
        table.add_column("target %", justify="right")
        table.add_column("rationale")
        for r in recs:
            style = action_style.get(r.get("action", ""), "white")
            tgt = r.get("target_weight_pct")
            table.add_row(
                r.get("ticker", "?"),
                f"[{style}]{r.get('action', '?')}[/{style}]",
                f"{(r.get('confidence') or 0):.2f}",
                f"{tgt:.1f}%" if isinstance(tgt, (int, float)) else "—",
                (r.get("rationale") or "")[:140],
            )
        console.print(table)

    critique = result.get("critique") or {}
    if critique:
        verdict = critique.get("verdict", "?")
        colour = "green" if verdict == "accept" else "yellow"
        console.print(
            f"\n[bold]Critic verdict:[/bold] [{colour}]{verdict}[/{colour}] "
            f"(score {critique.get('score', 0):.2f})"
        )
        for issue in (critique.get("issues") or [])[:5]:
            console.print(f"  • {issue}")


# ────────────────────────── login subcommands ────────────────────────


@login_app.command("groww")
def login_groww() -> None:
    """Authenticate with Groww (API key + secret) and store credentials locally.

    Opens the Groww developer portal, prompts for your API Key and Secret,
    validates them against Groww's token endpoint, and persists everything
    to ~/.pennywise/credentials.json.  Subsequent commands use the stored
    credentials automatically — no need to set GROWW_API_KEY in .env.
    """
    from pennywise.login import login_groww as _login_groww
    _login_groww(console)


if __name__ == "__main__":
    app()
