# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `auth_file_lock(path)`: an async context manager holding an exclusive cross-process lock on `<auth file>.lock`; it runs unlocked, with a warning, where no lock can be created.

### Fixed

- Providers sharing an auth file no longer fail with `token refresh failed: 401` after another provider refreshed. Each refresh rotates the refresh token, and a provider used to refresh from its own in-memory copy; it now refreshes under `auth_file_lock`, re-reads the file first and reuses credentials already refreshed by the same account when they are not older than its own.
- A refresh request already sent is finished and saved even when the caller is cancelled, and a failure to save the refreshed credentials no longer discards them, since the server has already revoked the previous refresh token.
- `save_credentials` writes atomically (temporary file, fsync, rename), keeps the file's permission bits and the other fields in it (the Codex CLI's `id_token`, `OPENAI_API_KEY`, `last_refresh`), updates a symlinked file through its target, and falls back to an in-place write when the rename is refused. `login()` writes under `auth_file_lock`.
- `load_credentials` takes the expiry from the access token's `exp` claim when the file has none, so credentials written by the Codex CLI are refreshed.

## [0.2.0] - 2026-09-26

### Added

- `CodexProvider.list_model_slugs()` and `get_model_capabilities()` fetch and cache the authenticated Codex model catalog.
- `CodexModelCapabilities` exposes context-window and automatic compaction limits.

### Fixed

- Preserve response ID, status, usage, and output items from terminal streaming events.
- Omit `max_output_tokens`, which the Codex backend rejects.

## [0.1.1] - 2026-09-13

### Added

- MIT license (`LICENSE.md`).
- Packaging metadata: `readme`, `license`, `keywords`, `classifiers`, and `[project.urls]` (Homepage, Repository, Documentation, Bug Tracker, Changelog).
- `.github/workflows/ci.yml`: lint, test, and build on push/PR.
- `Makefile` with `test`, `lint`, `format`, `build`, `upload_pypi`/`publish`/`release` targets.
- Expanded `.gitignore` to cover standard Python packaging/tooling artifacts.

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

[unreleased]: https://github.com/sonic182/llm-async-codex/compare/0.2.0...HEAD
[0.2.0]: https://github.com/sonic182/llm-async-codex/compare/0.1.1...0.2.0
[0.1.1]: https://github.com/sonic182/llm-async-codex/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/sonic182/llm-async-codex/releases/tag/0.1.0
