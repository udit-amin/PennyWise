"""CLI login flows for PennyWise.

``login_groww(console)``
    Interactive wizard: opens the Groww Trade API docs page, prompts for
    your API Key and either a Secret (checksum auth) or a TOTP code, validates
    against Groww's token endpoint, and persists to
    ``~/.pennywise/credentials.json``.

    Auth methods:
      checksum — API Key + Secret; PennyWise auto-refreshes silently at
                 6 AM IST every day. Run once.
      TOTP     — API Key + 6-digit code from your authenticator app; you
                 must re-run ``pennywise login groww`` each day after 6 AM IST.

``login_google(console)``
    Browser OAuth flow: opens the Google OAuth page, spins up a
    local HTTP server to receive the callback code, exchanges the
    code for tokens, and persists to ``~/.pennywise/credentials.json``.

    Requires ``GOOGLE_CLIENT_ID`` and ``GOOGLE_CLIENT_SECRET`` in the
    environment (or set via prompts).  Add
    ``http://localhost:18765/callback`` as an authorised redirect URI
    in your Google Cloud Console → Credentials → OAuth 2.0 Client.
"""
from __future__ import annotations

import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from rich.console import Console
from rich.prompt import Prompt

from pennywise import credentials as creds_mod
from pennywise.connectors.groww import exchange_for_access_token

GROWW_DOCS_URL = "https://groww.in/trade-api"

# Fixed port for Google OAuth local callback — add this to Google Cloud Console.
GOOGLE_CALLBACK_PORT = 18765
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


# ── Groww ─────────────────────────────────────────────────────────────


def login_groww(console: Console) -> None:
    """Interactive Groww credential setup wizard."""
    from pennywise.credentials import get_google_email, is_logged_in_google

    if not is_logged_in_google():
        console.print(
            "\n[red]Not signed in.[/red] Link your Groww account after signing into PennyWise.\n"
            "Run:  [bold]pennywise login google[/bold]  first.\n"
        )
        raise SystemExit(1)

    email = get_google_email()
    console.print(f"[dim]Signed in as {email}[/dim]\n")

    console.print(
        "\n[bold]PennyWise — Groww login[/bold]\n\n"
        "Groww provides two ways to authenticate with their Trade API:\n\n"
        "  [bold]1. Checksum[/bold] (recommended)\n"
        "     API Key + Secret → PennyWise refreshes your token silently every day.\n"
        "     Run this wizard once; no daily action needed.\n\n"
        "  [bold]2. TOTP[/bold]\n"
        "     API Key + 6-digit code from an authenticator app.\n"
        "     You must re-run this wizard each morning after 6 AM IST.\n\n"
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


# ── Google ────────────────────────────────────────────────────────────


def _receive_oauth_code(port: int, timeout: int = 120) -> str:
    """Start a local HTTP server, wait for /callback?code=..., return code."""
    result: dict[str, str | None] = {"code": None, "error": None}
    done = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            qs = parse_qs(urlparse(self.path).query)
            if "code" in qs:
                result["code"] = qs["code"][0]
                body = b"<html><body><h2>Login successful. You can close this tab.</h2></body></html>"
            elif "error" in qs:
                result["error"] = qs.get("error", ["unknown"])[0]
                body = f"<html><body><h2>Login failed: {result['error']}</h2></body></html>".encode()
            else:
                body = b"<html><body><p>Waiting...</p></body></html>"

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if result["code"] or result["error"]:
                done.set()

        def log_message(self, *args: object) -> None:  # suppress access logs
            pass

    srv = HTTPServer(("127.0.0.1", port), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    done.wait(timeout=timeout)
    srv.shutdown()

    if result["error"]:
        raise RuntimeError(f"Google OAuth error: {result['error']}")
    if not result["code"]:
        raise TimeoutError("Timed out waiting for Google OAuth callback.")
    return result["code"]  # type: ignore[return-value]


def login_google(console: Console) -> None:
    """Browser-based Google OAuth login flow."""
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id:
        console.print(
            "\n[yellow]GOOGLE_CLIENT_ID not set.[/yellow]\n"
            "Create OAuth 2.0 credentials at "
            "[link=https://console.cloud.google.com/apis/credentials]"
            "console.cloud.google.com/apis/credentials[/link]\n"
            "  Application type: [bold]Desktop app[/bold]\n"
            f"  Add authorised redirect URI: "
            f"[bold]http://localhost:{GOOGLE_CALLBACK_PORT}/callback[/bold]\n"
        )
        client_id = Prompt.ask("Google Client ID").strip()

    if not client_secret:
        client_secret = Prompt.ask("Google Client Secret", password=True).strip()

    if not client_id or not client_secret:
        console.print("[red]Client ID and Secret are required.[/red]")
        raise SystemExit(1)

    redirect_uri = f"http://localhost:{GOOGLE_CALLBACK_PORT}/callback"
    auth_url = (
        f"{GOOGLE_AUTH_URL}?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "access_type": "offline",
                "prompt": "consent",
            }
        )
    )

    console.print(
        f"\n[bold]PennyWise — Google login[/bold]\n\n"
        f"Opening your browser for Google sign-in…\n"
        f"[dim]If the browser doesn't open, visit:[/dim]\n{auth_url}\n"
    )
    webbrowser.open(auth_url)

    console.print(
        f"[dim]Waiting for callback on port {GOOGLE_CALLBACK_PORT} "
        f"(timeout 2 min)…[/dim]"
    )
    try:
        code = _receive_oauth_code(GOOGLE_CALLBACK_PORT, timeout=120)
    except TimeoutError:
        console.print("[red]Timed out. Run pennywise login google again.[/red]")
        raise SystemExit(1)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise SystemExit(1)

    console.print("[dim]Exchanging code for tokens…[/dim]")
    with httpx.Client() as client:
        resp = client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )

    if resp.status_code != 200:
        console.print(
            f"[red]Token exchange failed ({resp.status_code}): {resp.text}[/red]"
        )
        raise SystemExit(1)

    tokens = resp.json()
    id_tok = tokens.get("id_token")
    if not id_tok:
        console.print("[red]No id_token in Google response.[/red]")
        raise SystemExit(1)

    # Decode user info from the ID token (verify=False — for CLI display only;
    # signature already validated by Google's token endpoint).
    import base64, json as _json

    def _b64decode_unpadded(s: str) -> bytes:
        s += "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(s)

    payload_b64 = id_tok.split(".")[1]
    info = _json.loads(_b64decode_unpadded(payload_b64))

    email: str = info.get("email", "")
    name: str | None = info.get("name")
    picture: str | None = info.get("picture")

    creds_mod.set_google_credentials(
        email=email,
        name=name,
        picture=picture,
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        id_token=id_tok,
        expires_in=int(tokens.get("expires_in", 3600)),
    )

    console.print(
        f"\n[bold green]Signed in as {email}[/bold green]"
        + (f" ({name})" if name else "")
        + f"\n[dim]Credentials stored in {creds_mod.credentials_path()}[/dim]\n"
    )
