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

## Tool calling

Tool calling works through the normal streaming API: call `provider.acomplete(..., stream=True, tools=[...])`, drain `response.stream_content()`, then read `response.main_response.tool_calls`. Requires `llm-async>=0.5.2`.

## Limitations

- **Streaming only**: the Codex backend requires `stream=True`; non-streaming requests are rejected.
- **No stateless multi-turn**: the backend rejects `store=True` (`"Store must be set to false"`), so `previous_response_id`-based continuation (as used in some of `llm-async`'s other Responses API examples) does not work here. Resend the full conversation history (including `function_call`/`function_call_output` items) on every turn instead.
- **No automatic token refresh yet**: `CodexCredentials.refresh_token` is loaded and saved, but nothing renews an expired access token automatically.

## TODO

- Automatic token refresh.
