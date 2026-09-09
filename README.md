# llm-async-codex

ChatGPT Codex subscription provider for [`llm-async`](https://pypi.org/project/llm-async/).

```python
from llm_async_codex import CodexProvider

provider = CodexProvider.from_codex_home()
response = await provider.acomplete(
    model="gpt-5.5",
    messages=[{"role": "user", "content": "Review this repository."}],
)
```

This reads an existing Codex CLI login from `$CODEX_HOME/auth.json` or `~/.codex/auth.json`; it does not implement login or token refresh yet.
