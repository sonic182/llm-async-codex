import json

import pytest

from llm_async_codex import CodexAuthError, CodexProvider, load_credentials


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
    assert provider._default_headers() == {
        "Authorization": "Bearer access-token",
        "Content-Type": "application/json",
        "originator": "llm-async-codex",
        "ChatGPT-Account-Id": "account-id",
    }


def test_load_credentials_rejects_missing_access_token(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens": {}}', encoding="utf-8")

    with pytest.raises(CodexAuthError, match="no access token"):
        load_credentials(auth_path)
