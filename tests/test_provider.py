import asyncio
import time

from llm_async.providers.openai_responses import OpenAIResponsesProvider

from llm_async_codex import CodexCredentials, CodexProvider


def test_expired_credentials_trigger_refresh(monkeypatch, tmp_path):
    refresh_calls = []

    async def fake_refresh_credentials(credentials, auth_path):
        refresh_calls.append((credentials, auth_path))
        return CodexCredentials(
            access_token="new-access-token",
            refresh_token=credentials.refresh_token,
            expires_at=time.time() + 3600,
        )

    monkeypatch.setattr(
        "llm_async_codex.provider.refresh_credentials", fake_refresh_credentials
    )

    async def capture(*args, **kwargs):
        return kwargs

    monkeypatch.setattr(OpenAIResponsesProvider, "_single_complete", capture)

    auth_path = tmp_path / "auth.json"
    credentials = CodexCredentials(
        access_token="old-access-token",
        refresh_token="refresh-token",
        expires_at=time.time() - 10,
    )
    provider = CodexProvider(credentials, auth_path=auth_path)

    asyncio.run(provider._single_complete("model", [], True))

    assert len(refresh_calls) == 1
    assert provider.credentials.access_token == "new-access-token"
    assert provider.api_key == "new-access-token"
    assert provider._default_headers()["Authorization"] == "Bearer new-access-token"


def test_fresh_credentials_do_not_trigger_refresh(monkeypatch):
    refresh_calls = []

    async def fake_refresh_credentials(credentials, auth_path):
        refresh_calls.append((credentials, auth_path))
        raise AssertionError("should not refresh")

    monkeypatch.setattr(
        "llm_async_codex.provider.refresh_credentials", fake_refresh_credentials
    )

    async def capture(*args, **kwargs):
        return kwargs

    monkeypatch.setattr(OpenAIResponsesProvider, "_single_complete", capture)

    credentials = CodexCredentials(
        access_token="access-token",
        refresh_token="refresh-token",
        expires_at=time.time() + 3600,
    )
    provider = CodexProvider(credentials)

    asyncio.run(provider._single_complete("model", [], True))

    assert refresh_calls == []
    assert provider.api_key == "access-token"


def test_no_refresh_token_or_expiry_skips_refresh(monkeypatch):
    refresh_calls = []

    async def fake_refresh_credentials(credentials, auth_path):
        refresh_calls.append((credentials, auth_path))
        raise AssertionError("should not refresh")

    monkeypatch.setattr(
        "llm_async_codex.provider.refresh_credentials", fake_refresh_credentials
    )

    async def capture(*args, **kwargs):
        return kwargs

    monkeypatch.setattr(OpenAIResponsesProvider, "_single_complete", capture)

    provider = CodexProvider(CodexCredentials(access_token="access-token"))

    asyncio.run(provider._single_complete("model", [], True))

    assert refresh_calls == []
