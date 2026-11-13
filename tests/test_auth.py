import asyncio
import base64
import json
import os
from pathlib import Path

import pytest
from llm_async.providers.openai_responses import OpenAIResponsesProvider

from llm_async_codex import (
    CodexAuthError,
    CodexCredentials,
    CodexProvider,
    load_credentials,
    save_credentials,
)


def test_load_credentials_and_build_provider_headers(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps(
            {
                "tokens": {
                    "access_token": "access-token",
                    "refresh_token": "refresh-token",
                    "account_id": "account-id",
                }
            }
        ),
        encoding="utf-8",
    )

    provider = CodexProvider(load_credentials(auth_path))

    assert provider.base_url == "https://chatgpt.com/backend-api/codex"
    assert provider._messages_to_input([{"role": "user", "content": "OK"}]) == [
        {"role": "user", "content": [{"type": "input_text", "text": "OK"}]}
    ]
    assert provider._default_headers() == {
        "Authorization": "Bearer access-token",
        "Content-Type": "application/json",
        "originator": "llm-async-codex",
        "ChatGPT-Account-Id": "account-id",
    }


def test_provider_disables_response_storage(tmp_path, monkeypatch):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"tokens": {"access_token": "access-token"}}', encoding="utf-8"
    )
    provider = CodexProvider(load_credentials(auth_path))

    async def capture(*args, **kwargs):
        return kwargs

    monkeypatch.setattr(OpenAIResponsesProvider, "_single_complete", capture)

    assert asyncio.run(provider._single_complete("model", [], True)) == {"store": False}


def test_provider_requires_streaming(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        '{"tokens": {"access_token": "access-token"}}', encoding="utf-8"
    )
    provider = CodexProvider(load_credentials(auth_path))

    with pytest.raises(ValueError, match="require stream=True"):
        asyncio.run(provider._single_complete("model", [], False))


def test_expires_at_round_trips_through_save_and_load(tmp_path):
    auth_path = tmp_path / "auth.json"
    credentials = CodexCredentials(access_token="access-token", expires_at=1234567890.0)
    save_credentials(credentials, auth_path)

    loaded = load_credentials(auth_path)
    assert loaded.expires_at == 1234567890.0


def test_load_credentials_rejects_missing_access_token(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens": {}}', encoding="utf-8")

    with pytest.raises(CodexAuthError, match="no access token"):
        load_credentials(auth_path)


def test_save_credentials_replaces_atomically_and_falls_back_in_place(
    tmp_path, monkeypatch
):
    auth_path = tmp_path / "auth.json"
    save_credentials(CodexCredentials("access-1", "refresh-1"), auth_path)
    auth_path.chmod(0o640)
    renames = []
    real_replace = os.replace

    def spy(src, dst):
        renames.append(Path(dst))
        real_replace(src, dst)

    monkeypatch.setattr("llm_async_codex.auth.os.replace", spy)
    save_credentials(CodexCredentials("access-2", "refresh-2"), auth_path)
    assert renames == [auth_path]
    assert auth_path.stat().st_mode & 0o777 == 0o640

    def refuse(src, dst):
        raise OSError(16, "Device or resource busy")

    monkeypatch.setattr("llm_async_codex.auth.os.replace", refuse)
    save_credentials(CodexCredentials("access-3", "refresh-3"), auth_path)

    assert load_credentials(auth_path).refresh_token == "refresh-3"
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_through_a_symlink_keeps_the_link_and_other_cli_fields(tmp_path):
    target = tmp_path / "secrets" / "auth.json"
    target.parent.mkdir()
    target.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": "id",
                    "access_token": "a-1",
                    "refresh_token": "r-1",
                },
                "last_refresh": "2026-01-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    link = tmp_path / "auth.json"
    link.symlink_to(target)

    save_credentials(CodexCredentials("a-2", "r-2"), link)

    assert link.is_symlink()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["tokens"]["refresh_token"] == "r-2"
    assert saved["tokens"]["id_token"] == "id"
    assert saved["last_refresh"] == "2026-01-01T00:00:00Z"
    assert "OPENAI_API_KEY" in saved


def test_load_credentials_takes_expiry_from_the_access_token_when_absent(tmp_path):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": 1900000000}).encode())
    access_token = f"header.{payload.decode().rstrip('=')}.signature"
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"tokens": {"access_token": access_token, "refresh_token": "r"}}),
        encoding="utf-8",
    )

    assert load_credentials(auth_path).expires_at == 1900000000
