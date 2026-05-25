"""CLI login flow for PennyWise.

``login_groww(console)``
    Interactive wizard: opens the Groww Trade API docs page, prompts for
    your API Key and either a Secret (checksum auth) or a TOTP secret,
    validates against Groww's token endpoint, and persists to
    ``~/.pennywise/credentials.json``.

    Auth methods:
      checksum — API Key + Secret; PennyWise auto-refreshes silently at
                 6 AM IST every day. Run once.
      TOTP     — API Key + base32 TOTP secret; PennyWise generates the
                 6-digit codes automatically. Run once.
"""
from __future__ import annotations

import webbrowser

from rich.console import Console
from rich.prompt import Prompt

from pennywise import credentials as creds_mod
from pennywise.connectors.groww import exchange_for_access_token

GROWW_DOCS_URL = "https://groww.in/trade-api"


# ── Groww ─────────────────────────────────────────────────────────────


def login_groww(console: Console) -> None:
    """Interactive Groww credential setup wizard."""
    console.print(
        "\n[bold]PennyWise — Groww login[/bold]\n\n"
        "Groww provides two ways to authenticate with their Trade API:\n\n"
        "  [bold]1. Checksum[/bold] (recommended)\n"
        "     API Key + Secret → PennyWise refreshes your token silently every day.\n"
        "     Run this wizard once; no daily action needed.\n\n"
        "  [bold]2. TOTP[/bold]\n"
        "     API Key + base32 TOTP secret → PennyWise generates codes automatically.\n"
        "     Run this wizard once; no daily action needed.\n\n"
        f"Get your API credentials at: [link={GROWW_DOCS_URL}]{GROWW_DOCS_URL}[/link]\n"
        "  → Docs → Authentication → Cloud API Keys\n"
    )

    if webbrowser.open(GROWW_DOCS_URL):
        console.print("[dim]Opened Groww Trade API docs in your browser.[/dim]\n")

    method = Prompt.ask(
        "Auth method",
        choices=["checksum", "totp"],
        default="checksum",
    )

    api_key = Prompt.ask("Groww API Key (long JWT string)").strip()
    if not api_key:
        console.print("[red]API Key is required.[/red]")
        raise SystemExit(1)

    if method == "checksum":
        api_secret = Prompt.ask("Groww API Secret", password=True).strip()
        if not api_secret:
            console.print("[red]API Secret is required for checksum auth.[/red]")
            raise SystemExit(1)
        console.print("\n[dim]Validating credentials with Groww…[/dim]")
        try:
            access_token = exchange_for_access_token(api_key, api_secret)
        except Exception as exc:
            console.print(f"[red]Validation failed: {exc}[/red]")
            raise SystemExit(1)
        creds_mod.set_groww_credentials(
            api_key, api_secret, access_token=access_token, auth_method="checksum"
        )
        console.print(
            "\n[bold green]Groww credentials saved (checksum).[/bold green]\n"
            "Token will auto-refresh daily at 6 AM IST — no further action needed.\n"
            f"[dim]Stored in {creds_mod.credentials_path()}[/dim]\n"
        )
    else:
        console.print(
            "\n[dim]Enter the TOTP secret Groww showed you — the base32 string\n"
            "(e.g. GJ4AHT26CZVXPERD7M7XGAOOQK3LE5NN) that appears alongside\n"
            "the QR code. PennyWise generates the 6-digit codes automatically.[/dim]\n"
        )
        totp_secret = Prompt.ask("TOTP secret (base32)").strip().upper().replace(" ", "")
        if not totp_secret:
            console.print("[red]TOTP secret is required.[/red]")
            raise SystemExit(1)

        # Validate the secret is decodable before hitting Groww
        try:
            import base64
            base64.b32decode(totp_secret)
        except Exception:
            console.print(
                "[red]That doesn't look like a valid base32 secret. "
                "Check you copied the full string from Groww.[/red]"
            )
            raise SystemExit(1)

        # Generate current code and exchange immediately
        from pennywise.credentials import _totp
        totp_code = _totp(totp_secret)
        console.print(f"\n[dim]Generated TOTP code: {totp_code} — validating with Groww…[/dim]")
        try:
            access_token = exchange_for_access_token(api_key, totp_code=totp_code)
        except Exception as exc:
            console.print(f"[red]Validation failed: {exc}[/red]")
            raise SystemExit(1)
        creds_mod.set_groww_credentials(
            api_key, None, access_token=access_token,
            auth_method="totp", totp_secret=totp_secret,
        )
        console.print(
            "\n[bold green]Groww credentials saved (TOTP).[/bold green]\n"
            "PennyWise will auto-generate TOTP codes from the stored secret —\n"
            "no daily re-login needed.\n"
            f"[dim]Stored in {creds_mod.credentials_path()}[/dim]\n"
        )
