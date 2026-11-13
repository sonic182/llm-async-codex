import asyncio
import os
import time
from dataclasses import replace

import pytest
from llm_async.providers.openai_responses import OpenAIResponsesProvider

from llm_async_codex import (
    CodexCredentials,
    CodexLoginError,
    CodexModelCapabilities,
    CodexProvider,
    load_credentials,
    save_credentials,
)


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


def test_max_output_tokens_is_not_sent(monkeypatch):
    async def capture(*args, **kwargs):
        return kwargs

    monkeypatch.setattr(OpenAIResponsesProvider, "_single_complete", capture)
    provider = CodexProvider(CodexCredentials(access_token="access-token"))

    result = asyncio.run(
        provider._single_complete("model", [], True, max_output_tokens=1024)
    )

    assert "max_output_tokens" not in result


def test_model_catalog_is_parsed_and_cached(monkeypatch):
    requests = []

    async def fake_request(method, path):
        requests.append((method, path))
        return {
            "models": [
                {
                    "slug": "gpt-5.5",
                    "context_window": 272000,
                    "max_context_window": 400000,
                    "auto_compact_token_limit": 240000,
                },
                {"slug": "gpt-5.4"},
            ]
        }

    provider = CodexProvider(CodexCredentials(access_token="access-token"))
    monkeypatch.setattr(provider, "request", fake_request)

    capabilities = asyncio.run(provider.get_model_capabilities("gpt-5.5"))
    slugs = asyncio.run(provider.list_model_slugs())

    assert capabilities == CodexModelCapabilities(
        slug="gpt-5.5",
        context_window=272000,
        max_context_window=400000,
        auto_compact_token_limit=240000,
    )
    assert slugs == ["gpt-5.4", "gpt-5.5"]
    assert requests == [("GET", "/models?client_version=0.157.1")]


def test_stream_keeps_terminal_response_metadata(monkeypatch):
    item = {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "hi"}],
    }
    completed = {
        "id": "resp_1",
        "status": "completed",
        "output": [],
        "usage": {"total_tokens": 42},
    }

    async def fake_stream_json(*args, **kwargs):
        yield {"type": "response.output_text.delta", "delta": "hi"}
        yield {"type": "response.output_item.done", "item": item}
        yield {"type": "response.completed", "response": completed}

    monkeypatch.setattr("llm_async_codex.provider.stream_json", fake_stream_json)
    provider = CodexProvider(CodexCredentials(access_token="access-token"))
    response = provider._stream_responses_request("url", {}, {})

    async def drain():
        async for _ in response.stream_generator:
            pass

    asyncio.run(drain())

    assert response.original == {**completed, "output": [item]}
    assert response.main_response.content == "hi"


class _RotatingTokenServer:
    """Token endpoint that rotates the refresh token and rejects a used one, like OpenAI's."""

    def __init__(self, response_delay=0.0):
        self.calls = 0
        self.response_delay = response_delay
        self._current = "rt-0"

    async def refresh(self, refresh_token):
        await asyncio.sleep(0)
        if refresh_token != self._current:
            raise CodexLoginError("token refresh failed: 401")
        self.calls += 1
        self._current = f"rt-{self.calls}"
        await asyncio.sleep(self.response_delay)
        return {
            "access_token": f"at-{self.calls}",
            "refresh_token": self._current,
            "expires_in": 3600,
        }


def _expired_auth_file(tmp_path):
    auth_path = tmp_path / "auth.json"
    save_credentials(
        CodexCredentials("at-0", "rt-0", "acct", expires_at=0.0), auth_path
    )
    return auth_path


def test_providers_sharing_an_auth_file_never_reuse_a_rotated_token(
    monkeypatch, tmp_path
):
    server = _RotatingTokenServer()
    monkeypatch.setattr("llm_async_codex.oauth.refresh_access_token", server.refresh)
    auth_path = _expired_auth_file(tmp_path)

    async def scenario():
        first, second = (
            CodexProvider(load_credentials(auth_path), auth_path=auth_path)
            for _ in range(2)
        )
        await first._ensure_fresh_credentials()
        await second._ensure_fresh_credentials()
        stored = load_credentials(auth_path)
        save_credentials(replace(stored, expires_at=0.0), auth_path)
        concurrent = [
            CodexProvider(load_credentials(auth_path), auth_path=auth_path)
            for _ in range(3)
        ]
        await asyncio.gather(*(p._ensure_fresh_credentials() for p in concurrent))
        return [first, second, *concurrent]

    providers = asyncio.run(scenario())

    assert server.calls == 2
    assert [p.api_key for p in providers] == ["at-1", "at-1", "at-2", "at-2", "at-2"]
    assert load_credentials(auth_path).refresh_token == "rt-2"
    assert list(tmp_path.glob("*.tmp")) == []


def test_refresh_cancelled_after_the_server_rotated_still_saves_the_token(
    monkeypatch, tmp_path
):
    server = _RotatingTokenServer(response_delay=0.2)
    monkeypatch.setattr("llm_async_codex.oauth.refresh_access_token", server.refresh)
    auth_path = _expired_auth_file(tmp_path)
    provider = CodexProvider(load_credentials(auth_path), auth_path=auth_path)

    async def cancelled_refresh():
        await asyncio.wait_for(provider._ensure_fresh_credentials(), timeout=0.05)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(cancelled_refresh())

    assert load_credentials(auth_path).refresh_token == "rt-1"
    later = CodexProvider(load_credentials(auth_path), auth_path=auth_path)
    asyncio.run(later._ensure_fresh_credentials())
    assert server.calls == 1
    assert later.api_key == "at-1"


def test_failed_save_keeps_using_the_rotated_token_at_the_next_expiry(
    monkeypatch, tmp_path
):
    server = _RotatingTokenServer()
    monkeypatch.setattr("llm_async_codex.oauth.refresh_access_token", server.refresh)
    auth_path = _expired_auth_file(tmp_path)
    provider = CodexProvider(load_credentials(auth_path), auth_path=auth_path)

    def broken_save(credentials, path=None):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch:
        patch.setattr("llm_async_codex.oauth.save_credentials", broken_save)
        asyncio.run(provider._ensure_fresh_credentials())
    provider.credentials = replace(provider.credentials, expires_at=1.0)
    asyncio.run(provider._ensure_fresh_credentials())

    assert server.calls == 2
    assert provider.api_key == "at-2"


def test_a_file_holding_another_account_is_not_adopted(monkeypatch, tmp_path):
    server = _RotatingTokenServer()
    monkeypatch.setattr("llm_async_codex.oauth.refresh_access_token", server.refresh)
    auth_path = tmp_path / "auth.json"
    save_credentials(
        CodexCredentials("other-at", "other-rt", "other-account", time.time() + 3600),
        auth_path,
    )
    provider = CodexProvider(
        CodexCredentials("at-0", "rt-0", "acct", expires_at=0.0), auth_path=auth_path
    )

    asyncio.run(provider._ensure_fresh_credentials())

    assert server.calls == 1
    assert provider.api_key == "at-1"


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user for a read-only directory",
)
def test_auth_file_in_a_read_only_directory_still_refreshes(monkeypatch, tmp_path):
    server = _RotatingTokenServer()
    monkeypatch.setattr("llm_async_codex.oauth.refresh_access_token", server.refresh)
    auth_path = _expired_auth_file(tmp_path)
    provider = CodexProvider(load_credentials(auth_path), auth_path=auth_path)
    tmp_path.chmod(0o555)
    try:
        asyncio.run(provider._ensure_fresh_credentials())
    finally:
        tmp_path.chmod(0o755)

    assert provider.api_key == "at-1"
    assert load_credentials(auth_path).refresh_token == "rt-1"
