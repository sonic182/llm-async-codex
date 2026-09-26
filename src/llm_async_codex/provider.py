"""ChatGPT subscription provider for Codex Responses."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiosonic import HeadersType
from llm_async.models import Response
from llm_async.models.response import StreamChunk
from llm_async.providers.openai_responses import OpenAIResponsesProvider
from llm_async.utils.http import stream_json

from .auth import CodexCredentials, default_auth_path, load_credentials
from .oauth import TOKEN_REFRESH_SAFETY_MARGIN_SECONDS, refresh_credentials

CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
_MODELS_CLIENT_VERSION = "0.157.1"
_TERMINAL_EVENTS = {"response.completed", "response.incomplete", "response.failed"}


@dataclass(frozen=True, slots=True)
class CodexModelCapabilities:
    """Model limits returned by the Codex model catalog."""

    slug: str
    context_window: int
    max_context_window: int
    auto_compact_token_limit: int | None


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
        self._models_cache: list[dict[str, Any]] | None = None
        super().__init__(
            api_key=credentials.access_token, base_url=base_url, http2=http2
        )

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
                or time.time()
                < credentials.expires_at - TOKEN_REFRESH_SAFETY_MARGIN_SECONDS
            ):
                return
            self.credentials = await refresh_credentials(credentials, self.auth_path)
            self.api_key = self.credentials.access_token

    async def _single_complete(self, *args: Any, **kwargs: Any) -> Response:
        stream = args[2] if len(args) > 2 else kwargs.get("stream", False)
        if not stream:
            raise ValueError("Codex subscriptions require stream=True")
        kwargs.setdefault("store", False)
        kwargs.pop("max_output_tokens", None)
        await self._ensure_fresh_credentials()
        return await super()._single_complete(*args, **kwargs)

    def _stream_responses_request(
        self, url: str, payload: dict[str, Any], headers: HeadersType
    ) -> Response:
        # ponytail: remove this override once llm-async preserves terminal response metadata.
        response = Response(
            {}, self.__class__.name(), stream=True, stream_generator=None
        )

        async def _gen():
            accumulated_items: list[dict[str, Any]] = []
            async for chunk in stream_json(
                self.client,
                url,
                payload,
                headers,
                retry_config=self.retry_config,
            ):
                if not isinstance(chunk, dict):
                    continue
                chunk_type = chunk.get("type")
                if chunk_type == "response.output_item.done" and isinstance(
                    chunk.get("item"), dict
                ):
                    accumulated_items.append(chunk["item"])
                elif chunk_type in _TERMINAL_EVENTS and isinstance(
                    chunk.get("response"), dict
                ):
                    response.original = {
                        **chunk["response"],
                        "output": accumulated_items,
                    }
                delta_text = self._extract_stream_text(chunk)
                if delta_text:
                    yield StreamChunk(delta_text, chunk)
            response.main_response = self._parse_response(
                response.original or {"output": accumulated_items}
            )

        response.stream_generator = _gen()
        return response

    async def _ensure_models_cache(self) -> list[dict[str, Any]]:
        await self._ensure_fresh_credentials()
        if self._models_cache is None:
            payload = await self.request(
                "GET", f"/models?client_version={_MODELS_CLIENT_VERSION}"
            )
            models = payload.get("models") if isinstance(payload, dict) else None
            self._models_cache = (
                [entry for entry in models if isinstance(entry, dict)]
                if isinstance(models, list)
                else []
            )
        return self._models_cache

    async def get_model_capabilities(self, model: str) -> CodexModelCapabilities | None:
        """Return capabilities for a model in the authenticated catalog."""
        for entry in await self._ensure_models_cache():
            if entry.get("slug") != model:
                continue
            context_window = entry.get("context_window")
            max_context_window = entry.get("max_context_window")
            if not isinstance(context_window, int) or not isinstance(
                max_context_window, int
            ):
                return None
            auto_compact = entry.get("auto_compact_token_limit")
            return CodexModelCapabilities(
                slug=model,
                context_window=context_window,
                max_context_window=max_context_window,
                auto_compact_token_limit=(
                    auto_compact if isinstance(auto_compact, int) else None
                ),
            )
        return None

    async def list_model_slugs(self) -> list[str]:
        """Return model slugs in the authenticated catalog."""
        return sorted(
            entry["slug"]
            for entry in await self._ensure_models_cache()
            if isinstance(entry.get("slug"), str) and entry["slug"]
        )

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
