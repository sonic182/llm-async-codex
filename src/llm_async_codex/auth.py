"""Load credentials from an existing Codex CLI login."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CodexAuthError(ValueError):
    """Raised when Codex credentials cannot be loaded."""


@dataclass(frozen=True, slots=True)
class CodexCredentials:
    """ChatGPT OAuth credentials used by the Codex Responses endpoint."""

    access_token: str
    refresh_token: str | None = None
    account_id: str | None = None


def default_auth_path() -> Path:
    """Return the auth file used by the Codex CLI."""
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "auth.json"


def load_credentials(path: Path | None = None) -> CodexCredentials:
    """Load credentials from a Codex CLI auth file."""
    auth_path = path or default_auth_path()
    try:
        value: Any = json.loads(auth_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise CodexAuthError(f"cannot read Codex auth file: {auth_path}") from error
    except json.JSONDecodeError as error:
        raise CodexAuthError(f"invalid JSON in Codex auth file: {auth_path}") from error

    if not isinstance(value, Mapping) or not isinstance(value.get("tokens"), Mapping):
        raise CodexAuthError("Codex auth file has no tokens object")

    tokens = value["tokens"]
    access_token = tokens.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise CodexAuthError("Codex auth file has no access token")

    refresh_token = _string_or_none(tokens.get("refresh_token"))
    account_id = _string_or_none(tokens.get("account_id")) or _string_or_none(
        value.get("account_id")
    )
    return CodexCredentials(access_token, refresh_token, account_id)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
