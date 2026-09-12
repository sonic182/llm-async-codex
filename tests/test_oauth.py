import asyncio
import base64
import hashlib
import json
import threading
import urllib.request
from urllib.parse import parse_qs, urlparse

import aiosonic
import pytest

from llm_async_codex import load_credentials
from llm_async_codex.oauth import (
    OAUTH_PORT,
    CodexLoginError,
    extract_account_id,
    login_with_browser,
    login_with_device_code,
)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _fake_jwt(payload: dict) -> str:
    header = _b64url(b'{"alg":"none"}')
    body = _b64url(json.dumps(payload).encode())
    return f"{header}.{body}.sig"


def test_generate_pkce_challenge_matches_sha256():
    from llm_async_codex.oauth import _generate_pkce

    verifier, challenge = _generate_pkce()
    expected = _b64url(hashlib.sha256(verifier.encode()).digest())
    assert challenge == expected


@pytest.mark.parametrize(
    "claims",
    [
        {"chatgpt_account_id": "acc-1"},
        {"https://api.openai.com/auth": {"chatgpt_account_id": "acc-1"}},
        {"organizations": [{"id": "acc-1"}]},
    ],
)
def test_extract_account_id_covers_claim_shapes(claims):
    tokens = {"id_token": _fake_jwt(claims)}
    assert extract_account_id(tokens) == "acc-1"


class _FakeResponse:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = json.dumps(body)

    async def text(self) -> str:
        return self._body


def test_login_with_browser_round_trips_credentials(monkeypatch, tmp_path):
    captured_url = {}

    def fake_open(url: str) -> bool:
        captured_url["url"] = url
        state = parse_qs(urlparse(url).query)["state"][0]
        callback_url = f"http://localhost:{OAUTH_PORT}/auth/callback?code=test-code&state={state}"
        threading.Thread(
            target=lambda: urllib.request.urlopen(callback_url), daemon=True
        ).start()
        return True

    monkeypatch.setattr("llm_async_codex.oauth.webbrowser.open", fake_open)

    async def fake_post(self, url, data=None, headers=None, json=None, **kwargs):
        assert url == "https://auth.openai.com/oauth/token"
        assert data["code"] == "test-code"
        return _FakeResponse(
            200,
            {
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": _fake_jwt({"chatgpt_account_id": "account-id"}),
            },
        )

    monkeypatch.setattr(aiosonic.HTTPClient, "post", fake_post)

    auth_path = tmp_path / "auth.json"
    credentials = asyncio.run(login_with_browser(auth_path))

    assert credentials.access_token == "access-token"
    assert credentials.refresh_token == "refresh-token"
    assert credentials.account_id == "account-id"
    assert captured_url["url"]
    assert load_credentials(auth_path) == credentials


def test_login_with_device_code_polls_until_ready(monkeypatch, tmp_path):
    real_sleep = asyncio.sleep
    monkeypatch.setattr("llm_async_codex.oauth.asyncio.sleep", lambda _: real_sleep(0))

    poll_calls = {"count": 0}

    async def fake_post(self, url, data=None, headers=None, json=None, **kwargs):
        if url.endswith("/deviceauth/usercode"):
            return _FakeResponse(
                200, {"device_auth_id": "device-1", "user_code": "ABCD-EFGH", "interval": 1}
            )
        if url.endswith("/deviceauth/token"):
            poll_calls["count"] += 1
            if poll_calls["count"] == 1:
                return _FakeResponse(403, {})
            return _FakeResponse(
                200, {"authorization_code": "auth-code", "code_verifier": "server-verifier"}
            )
        if url.endswith("/oauth/token"):
            assert data["code_verifier"] == "server-verifier"
            return _FakeResponse(
                200,
                {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "id_token": _fake_jwt({"chatgpt_account_id": "account-id"}),
                },
            )
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr(aiosonic.HTTPClient, "post", fake_post)

    auth_path = tmp_path / "auth.json"
    credentials = asyncio.run(login_with_device_code(auth_path))

    assert poll_calls["count"] == 2
    assert credentials.access_token == "access-token"
    assert load_credentials(auth_path) == credentials


def test_login_with_browser_raises_on_timeout(monkeypatch, tmp_path):
    monkeypatch.setattr("llm_async_codex.oauth.webbrowser.open", lambda url: True)
    monkeypatch.setattr("llm_async_codex.oauth.CALLBACK_TIMEOUT_SECONDS", 0)

    with pytest.raises(CodexLoginError, match="timed out"):
        asyncio.run(login_with_browser(tmp_path / "auth.json"))
