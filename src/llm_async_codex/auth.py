"""Load credentials from an existing Codex CLI login."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import stat
import tempfile
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

logger = logging.getLogger("llm_async_codex.auth")

_LOCK_POLL_SECONDS = 0.05


class CodexAuthError(ValueError):
    """Raised when Codex credentials cannot be loaded."""


@dataclass(frozen=True, slots=True)
class CodexCredentials:
    """ChatGPT OAuth credentials used by the Codex Responses endpoint."""

    access_token: str
    refresh_token: str | None = None
    account_id: str | None = None
    expires_at: float | None = None


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
    expires_at = tokens.get("expires_at")
    if not isinstance(expires_at, (int, float)):
        expires_at = None
    return CodexCredentials(access_token, refresh_token, account_id, expires_at)


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def save_credentials(credentials: CodexCredentials, path: Path | None = None) -> Path:
    """Write credentials to a Codex-CLI-compatible auth file. Returns the path written.

    The file is written to a temporary sibling and renamed into place, so a concurrent reader never
    sees it half-written; it keeps the existing file's permission bits. When the rename is refused
    (for example a bind-mounted single file, or a file held open on Windows) it is written in place.
    """
    auth_path = path or default_auth_path()
    auth_path.parent.mkdir(parents=True, exist_ok=True)
    tokens: dict[str, Any] = {"access_token": credentials.access_token}
    if credentials.refresh_token:
        tokens["refresh_token"] = credentials.refresh_token
    if credentials.account_id:
        tokens["account_id"] = credentials.account_id
    if credentials.expires_at is not None:
        tokens["expires_at"] = credentials.expires_at
    content = json.dumps({"tokens": tokens}, indent=2)
    try:
        _replace_atomically(auth_path, content)
    except OSError:
        logger.warning(
            "Could not replace %s atomically; writing it in place",
            auth_path,
            exc_info=True,
        )
        auth_path.write_text(content, encoding="utf-8")
    return auth_path


def _replace_atomically(auth_path: Path, content: str) -> None:
    handle, temp_name = tempfile.mkstemp(
        dir=auth_path.parent, prefix=f".{auth_path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        with contextlib.suppress(FileNotFoundError):
            os.chmod(temp_name, stat.S_IMODE(os.stat(auth_path).st_mode))
        os.replace(temp_name, auth_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
    _fsync_directory(auth_path.parent)


def _fsync_directory(directory: Path) -> None:
    # Makes the rename durable; directories cannot be opened this way on Windows.
    if fcntl is None:  # pragma: no cover - Windows
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        logger.debug("Could not fsync %s", directory, exc_info=True)


@contextlib.asynccontextmanager
async def auth_file_lock(path: Path | None = None) -> AsyncIterator[None]:
    """Hold an exclusive lock on ``<auth file>.lock`` across processes and providers.

    Every token refresh rotates the refresh token, so providers that share an auth file must
    refresh one at a time. The lock is polled without blocking, so a cancelled waiter leaves
    nothing behind holding it.
    """
    auth_path = path or default_auth_path()
    lock_path = auth_path.with_name(f"{auth_path.name}.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as error:
        raise CodexAuthError(
            f"cannot open lock file {lock_path}: {error}; "
            "the directory holding the Codex auth file must be writable"
        ) from error
    try:
        while not _try_lock(fd):
            await asyncio.sleep(_LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


def _try_lock(fd: int) -> bool:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:  # pragma: no cover - Windows
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    except (BlockingIOError, PermissionError):
        return False
    return True


def _unlock(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:  # pragma: no cover - Windows
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
