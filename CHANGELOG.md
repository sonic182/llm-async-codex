# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-13

### Added

- `CodexProvider`: send [`llm-async`](https://pypi.org/project/llm-async/) Responses API requests through a ChatGPT Codex subscription.
- `load_credentials`/`default_auth_path`: read an existing Codex CLI login from `$CODEX_HOME/auth.json` or `~/.codex/auth.json`, or from any explicit path.
- Login support, so this package doesn't depend on a separate Codex CLI install:
  - Browser-based OAuth (PKCE, localhost callback) via `login_with_browser`.
  - Device-code flow (headless/SSH, no local browser needed) via `login_with_device_code`.
  - `llm-async-codex login [--device-code] [--auth-file PATH] [--verbose]` CLI command.
- Automatic token refresh: `CodexCredentials` tracks expiry, and `CodexProvider` transparently refreshes and persists a new access token before the current one expires.
- Tool calling support through the normal streaming API (`response.main_response.tool_calls`); requires `llm-async>=0.5.2`.
- Optional HTTP/2 support (`CodexProvider(..., http2=True)`).
- `scripts/test_tool_calling.py`: a smoke-test script exercising a full tool-calling round trip against a real login.

[unreleased]: https://github.com/sonic182/llm-async-codex/compare/0.1.0...HEAD
[0.1.0]: https://github.com/sonic182/llm-async-codex/releases/tag/0.1.0
