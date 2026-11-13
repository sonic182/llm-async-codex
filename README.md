# llm-async-codex

ChatGPT Codex subscription provider for [`llm-async`](https://pypi.org/project/llm-async/).

```python
from llm_async_codex import CodexProvider

provider = CodexProvider.from_codex_home()
response = await provider.acomplete(
    model="gpt-5.5",
    messages=[{"role": "user", "content": "Review this repository."}],
    stream=True,
)
async for chunk in response.stream_content():
    print(chunk, end="", flush=True)
```

This reads an existing Codex CLI login from `$CODEX_HOME/auth.json` or `~/.codex/auth.json` by default (`CodexProvider.from_codex_home()`), or a `CodexCredentials` loaded from any path via `load_credentials(path)`.

## Login

If you don't already have a Codex CLI login, log in directly:

```
llm-async-codex login
llm-async-codex login --device-code   # headless/SSH, no local browser needed
llm-async-codex login --auth-file ~/.my-tool/auth.json
llm-async-codex login --verbose
```

or from Python: `await login(device_code=False, auth_path=None, verbose=False)`.

## Token refresh and shared auth files

Access tokens are refreshed automatically shortly before they expire. Each refresh also rotates the
refresh token and revokes the previous one, so several providers (or processes) using the same auth
file coordinate through it:

- a refresh holds an exclusive lock on `<auth file>.lock` next to the auth file (next to its
  target, for a symlink);
- under the lock it re-reads the file and reuses credentials another provider already refreshed,
  as long as they belong to the same account and are not older than its own;
- the file is written to a temporary sibling and renamed into place, keeping its permission bits
  and any other fields in it (such as the Codex CLI's `id_token`), and written in place only if the
  rename is refused;
- a refresh request already sent is finished and kept even if the caller is cancelled or saving fails.

Pass the same `auth_path` to every provider that shares a login (without one, the default
`~/.codex/auth.json` is used). Keep the auth file's directory writable: without it there is no lock,
and refreshes coordinate only within one process (a warning is logged). `login()` takes the lock;
use `auth_file_lock(path)` around your own writes to the file. The Codex CLI itself does not take
this lock, so sharing its file only works through the re-read, and the lock may not be shared across
network or VM file shares. A file without a stored expiry (as the Codex CLI writes) takes it from the
access token's `exp` claim.

## Tool calling

Tool calling works through the normal streaming API: call `provider.acomplete(..., stream=True, tools=[...])`, drain `response.stream_content()`, then read `response.main_response.tool_calls`. Requires `llm-async>=0.5.2`.

## Model catalog

`await provider.list_model_slugs()` lists models available to the authenticated account.
`await provider.get_model_capabilities(model)` returns its context-window and automatic
compaction limits when present.

## Limitations

- **Streaming only**: the Codex backend requires `stream=True`; non-streaming requests are rejected.
- **No stateless multi-turn**: the backend rejects `store=True` (`"Store must be set to false"`), so `previous_response_id`-based continuation (as used in some of `llm-async`'s other Responses API examples) does not work here. Resend the full conversation history (including `function_call`/`function_call_output` items) on every turn instead.
- `max_output_tokens` is ignored because the Codex backend rejects it.
