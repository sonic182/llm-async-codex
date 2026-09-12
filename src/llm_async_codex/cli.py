"""Command-line entry point for llm-async-codex."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .oauth import CodexLoginError, login


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="llm-async-codex")
    subparsers = parser.add_subparsers(dest="command", required=True)

    login_parser = subparsers.add_parser("login", help="Log in to ChatGPT Codex")
    login_parser.add_argument(
        "--device-code",
        action="store_true",
        help="Use the device-code flow instead of opening a browser",
    )
    login_parser.add_argument(
        "--auth-file",
        type=Path,
        default=None,
        help="Where to write credentials (default: $CODEX_HOME/auth.json or ~/.codex/auth.json)",
    )
    login_parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress (HTTP statuses, full URLs, poll attempts)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command == "login":
        try:
            credentials = asyncio.run(
                login(
                    device_code=args.device_code,
                    auth_path=args.auth_file,
                    verbose=args.verbose,
                )
            )
        except CodexLoginError as error:
            print(f"Login failed: {error}")
            return 1
        masked = f"{credentials.access_token[:8]}..."
        print(f"Logged in (access token {masked}).")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
