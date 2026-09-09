"""ChatGPT subscription provider for Codex Responses."""

from __future__ import annotations

from pathlib import Path

from llm_async.providers.openai_responses import OpenAIResponsesProvider

from .auth import CodexCredentials, load_credentials

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class CodexProvider(OpenAIResponsesProvider):
    """Send Responses API requests through a ChatGPT Codex subscription."""

    def __init__(
        self,
        credentials: CodexCredentials,
        *,
        base_url: str = CODEX_BASE_URL,
    ) -> None:
        self.credentials = credentials
        super().__init__(api_key=credentials.access_token, base_url=base_url)

    @classmethod
    def from_codex_home(cls, path: Path | None = None) -> CodexProvider:
        """Create a provider from an existing Codex CLI login."""
        return cls(load_credentials(path))

    def _default_headers(self) -> dict[str, str]:
        headers = super()._default_headers()
        headers["originator"] = "llm-async-codex"
        if self.credentials.account_id:
            headers["ChatGPT-Account-Id"] = self.credentials.account_id
        return headers
