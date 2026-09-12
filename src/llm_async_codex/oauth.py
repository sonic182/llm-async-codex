"""ChatGPT Codex login: browser OAuth (PKCE) and device-code flows."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import webbrowser
from collections.abc import Mapping
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import aiosonic

from .auth import CodexCredentials, save_credentials

logger = logging.getLogger("llm_async_codex.oauth")

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
OAUTH_PORT = 1455
REDIRECT_URI = f"http://localhost:{OAUTH_PORT}/auth/callback"
ORIGINATOR = "llm-async-codex"
CALLBACK_TIMEOUT_SECONDS = 300
DEVICE_POLL_SAFETY_MARGIN_SECONDS = 3
TOKEN_REFRESH_SAFETY_MARGIN_SECONDS = 60


class CodexLoginError(RuntimeError):
    """Raised when the login flow fails."""


def _configure_logging(verbose: bool) -> None:
    """Ensure login progress is visible by default, with more detail if verbose."""
    if not logging.getLogger().handlers:
        logging.basicConfig(format="%(message)s", level=logging.INFO)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _generate_pkce() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for an OAuth PKCE exchange."""
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _build_authorize_url(state: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email offline_access",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": ORIGINATOR,
    }
    return f"{ISSUER}/oauth/authorize?{urlencode(params)}"


def parse_jwt_claims(token: str) -> dict[str, Any] | None:
    """Decode (without verifying) the claims payload of a JWT."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    padding = "=" * (-len(parts[1]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(parts[1] + padding))
    except (ValueError, json.JSONDecodeError):
        return None


def _compute_expires_at(tokens: Mapping[str, Any]) -> float | None:
    expires_in = tokens.get("expires_in")
    if not isinstance(expires_in, (int, float)):
        return None
    return time.time() + expires_in


def extract_account_id(tokens: Mapping[str, Any]) -> str | None:
    """Extract the ChatGPT account id from a token response's claims."""
    for token_key in ("id_token", "access_token"):
        token = tokens.get(token_key)
        if not isinstance(token, str):
            continue
        claims = parse_jwt_claims(token)
        if not claims:
            continue
        account_id = (
            claims.get("chatgpt_account_id")
            or claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
            or (claims.get("organizations") or [{}])[0].get("id")
        )
        if account_id:
            return account_id
    return None


async def _exchange_code_for_tokens(
    client: aiosonic.HTTPClient,
    *,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    response = await client.post(
        f"{ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        },
    )
    logger.debug("Token exchange response: %s", response.status_code)
    if response.status_code != 200:
        raise CodexLoginError(f"token exchange failed: {response.status_code}")
    return json.loads(await response.text())


class _CallbackResult:
    code: str | None = None
    state: str | None = None
    error: str | None = None


def _make_callback_handler(result: _CallbackResult) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(parsed.query)
            result.code = query.get("code", [None])[0]
            result.state = query.get("state", [None])[0]
            result.error = query.get("error", [None])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            message = (
                "Login failed, you can close this window."
                if result.error
                else "Login successful, you can close this window."
            )
            self.wfile.write(f"<html><body>{message}</body></html>".encode())

        def log_message(self, format: str, *args: Any) -> None:
            return

    return Handler


async def login_with_browser(
    auth_path: Path | None = None, *, verbose: bool = False
) -> CodexCredentials:
    """Log in via the browser-based OAuth flow, opening a localhost listener."""
    _configure_logging(verbose)
    code_verifier, code_challenge = _generate_pkce()
    state = secrets.token_urlsafe(24)

    result = _CallbackResult()
    server = HTTPServer(("localhost", OAUTH_PORT), _make_callback_handler(result))
    server.timeout = CALLBACK_TIMEOUT_SECONDS

    authorize_url = _build_authorize_url(state, code_challenge)
    logger.debug("Authorize URL: %s", authorize_url)
    logger.info("Listening on %s ...", REDIRECT_URI)
    if webbrowser.open(authorize_url):
        logger.info("Opened browser for ChatGPT login. Complete the login there.")
    else:
        logger.info(
            "Could not open a browser automatically. Open this URL to log in:\n%s",
            authorize_url,
        )

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, server.handle_request)
    finally:
        server.server_close()

    if result.code is None:
        raise CodexLoginError(result.error or "OAuth callback timed out")
    if result.state != state:
        raise CodexLoginError("invalid OAuth state - potential CSRF")

    logger.info("Received callback, exchanging code for tokens ...")
    async with aiosonic.HTTPClient() as client:
        tokens = await _exchange_code_for_tokens(
            client,
            code=result.code,
            redirect_uri=REDIRECT_URI,
            code_verifier=code_verifier,
        )

    credentials = CodexCredentials(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        account_id=extract_account_id(tokens),
        expires_at=_compute_expires_at(tokens),
    )
    resolved_path = save_credentials(credentials, auth_path)
    logger.info("Saved credentials to %s", resolved_path)
    return credentials


async def login_with_device_code(
    auth_path: Path | None = None, *, verbose: bool = False
) -> CodexCredentials:
    """Log in via OpenAI's device-code flow (no local server required)."""
    _configure_logging(verbose)
    async with aiosonic.HTTPClient() as client:
        usercode_response = await client.post(
            f"{ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
        logger.debug("Device usercode response: %s", usercode_response.status_code)
        if usercode_response.status_code != 200:
            raise CodexLoginError("failed to start device authorization")
        device_data = json.loads(await usercode_response.text())
        device_auth_id = device_data["device_auth_id"]
        user_code = device_data["user_code"]
        interval = max(int(device_data.get("interval") or 5), 1)

        logger.info("Enter code: %s", user_code)
        logger.info("Then visit: %s/codex/device", ISSUER)

        attempt = 0
        while True:
            attempt += 1
            await asyncio.sleep(interval + DEVICE_POLL_SAFETY_MARGIN_SECONDS)
            poll_response = await client.post(
                f"{ISSUER}/api/accounts/deviceauth/token",
                json={"device_auth_id": device_auth_id, "user_code": user_code},
                headers={"Content-Type": "application/json"},
            )
            logger.debug(
                "Poll attempt %d: status %s", attempt, poll_response.status_code
            )
            if poll_response.status_code == 200:
                break
            if poll_response.status_code not in (403, 404):
                raise CodexLoginError(
                    f"device authorization failed: {poll_response.status_code}"
                )
            logger.info("Still waiting for confirmation ...")

        poll_data = json.loads(await poll_response.text())
        logger.info("Confirmed, exchanging code for tokens ...")
        tokens = await _exchange_code_for_tokens(
            client,
            code=poll_data["authorization_code"],
            redirect_uri=f"{ISSUER}/deviceauth/callback",
            code_verifier=poll_data["code_verifier"],
        )

    credentials = CodexCredentials(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token"),
        account_id=extract_account_id(tokens),
        expires_at=_compute_expires_at(tokens),
    )
    resolved_path = save_credentials(credentials, auth_path)
    logger.info("Saved credentials to %s", resolved_path)
    return credentials


async def refresh_access_token(refresh_token: str) -> dict[str, Any]:
    """Exchange a refresh token for a new access token."""
    async with aiosonic.HTTPClient() as client:
        response = await client.post(
            f"{ISSUER}/oauth/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
        logger.debug("Token refresh response: %s", response.status_code)
        if response.status_code != 200:
            raise CodexLoginError(f"token refresh failed: {response.status_code}")
        return json.loads(await response.text())


async def refresh_credentials(
    credentials: CodexCredentials, auth_path: Path | None = None
) -> CodexCredentials:
    """Refresh an access token and persist the result."""
    if not credentials.refresh_token:
        raise CodexLoginError("no refresh token available")

    tokens = await refresh_access_token(credentials.refresh_token)
    refreshed = CodexCredentials(
        access_token=tokens["access_token"],
        refresh_token=tokens.get("refresh_token") or credentials.refresh_token,
        account_id=extract_account_id(tokens) or credentials.account_id,
        expires_at=_compute_expires_at(tokens),
    )
    resolved_path = save_credentials(refreshed, auth_path)
    logger.debug("Refreshed credentials saved to %s", resolved_path)
    return refreshed


async def login(
    *, device_code: bool = False, auth_path: Path | None = None, verbose: bool = False
) -> CodexCredentials:
    """Log in to ChatGPT Codex, via browser by default or device code."""
    if device_code:
        return await login_with_device_code(auth_path, verbose=verbose)
    return await login_with_browser(auth_path, verbose=verbose)
