"""ChatGPT subscription provider for Codex Responses."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from llm_async.models import Response
from llm_async.providers.openai_responses import OpenAIResponsesProvider

from .auth import CodexCredentials, default_auth_path, load_credentials
from .oauth import TOKEN_REFRESH_SAFETY_MARGIN_SECONDS, refresh_credentials

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"


class CodexProvider(OpenAIResponsesProvider):
    """Send Responses API requests through a ChatGPT Codex subscription."""

    def __init__(
        self,
        credentials: CodexCredentials,
        *,
        base_url: str = CODEX_BASE_URL,
        http2: bool = False,
        auth_path: Path | None = None,
    ) -> None:
        self.credentials = credentials
        self.auth_path = auth_path
        self._refresh_lock = asyncio.Lock()
        super().__init__(api_key=credentials.access_token, base_url=base_url, http2=http2)

    @classmethod
    def from_codex_home(cls, path: Path | None = None) -> CodexProvider:
        """Create a provider from an existing Codex CLI login."""
        return cls(load_credentials(path), auth_path=path or default_auth_path())

    async def _ensure_fresh_credentials(self) -> None:
        credentials = self.credentials
        if not credentials.refresh_token or credentials.expires_at is None:
            return
        if time.time() < credentials.expires_at - TOKEN_REFRESH_SAFETY_MARGIN_SECONDS:
            return

        async with self._refresh_lock:
            credentials = self.credentials
            if (
                not credentials.refresh_token
                or credentials.expires_at is None
                or time.time() < credentials.expires_at - TOKEN_REFRESH_SAFETY_MARGIN_SECONDS
            ):
                return
            self.credentials = await refresh_credentials(credentials, self.auth_path)
            self.api_key = self.credentials.access_token

    async def _single_complete(self, *args: Any, **kwargs: Any) -> Response:
        stream = args[2] if len(args) > 2 else kwargs.get("stream", False)
        if not stream:
            raise ValueError("Codex subscriptions require stream=True")
        kwargs.setdefault("store", False)
        await self._ensure_fresh_credentials()
        return await super()._single_complete(*args, **kwargs)

    def _messages_to_input(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Convert text messages to the list-only Codex Responses input format."""
        input_items = super()._messages_to_input(messages)
        if isinstance(input_items, str):
            return [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": input_items}],
                }
            ]

        normalized: list[dict[str, Any]] = []
        for item in input_items:
            if item.get("role") == "user" and isinstance(item.get("content"), str):
                normalized.append(
                    {
                        **item,
                        "content": [{"type": "input_text", "text": item["content"]}],
                    }
                )
            elif item.get("role") == "assistant" and isinstance(
                item.get("content"), str
            ):
                normalized.append(
                    {
                        **item,
                        "content": [{"type": "output_text", "text": item["content"]}],
                    }
                )
            else:
                normalized.append(item)
        return normalized

    def _default_headers(self) -> dict[str, str]:
        headers = super()._default_headers()
        headers["originator"] = "llm-async-codex"
        if self.credentials.account_id:
            headers["ChatGPT-Account-Id"] = self.credentials.account_id
        return headers
