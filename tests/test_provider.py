import asyncio
import time

from llm_async.providers.openai_responses import OpenAIResponsesProvider

from llm_async_codex import CodexCredentials, CodexModelCapabilities, CodexProvider


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
