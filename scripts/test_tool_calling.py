"""Smoke test: verify tool calling works end-to-end against a live Codex login.

Usage:
    .venv/bin/python scripts/test_tool_calling.py [path/to/auth.json]

Requires a real auth.json (default: ./auth.json) produced by `llm-async-codex login`.

Uses the normal streaming API (provider.acomplete(..., stream=True) +
response.main_response.tool_calls), which requires the streaming tool-call fix
in the local ../llm-async checkout (see pyproject.toml's path dependency).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from llm_async.models import Tool

from llm_async_codex import CodexProvider, load_credentials

MODEL = "gpt-5.6-luna"

ADD_NUMBERS_TOOL = Tool(
    name="add_numbers",
    description="Add two integers together.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
)


def add_numbers(a: int, b: int) -> int:
    return a + b


async def main() -> int:
    auth_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("auth.json")
    provider = CodexProvider(load_credentials(auth_path), http2=True, auth_path=auth_path)

    question = (
        "What is 482193 + 917364? Use the add_numbers tool to compute it, "
        "then tell me the result."
    )
    response = await provider.acomplete(
        model=MODEL,
        messages=[{"role": "user", "content": question}],
        tools=[ADD_NUMBERS_TOOL],
        tool_choice="required",
        stream=True,
    )
    async for _ in response.stream_content():
        pass

    main_response = response.main_response
    if not main_response or not main_response.tool_calls:
        print("FAIL: model did not request any tool calls")
        return 1

    tool_call = main_response.tool_calls[0]
    print(f"Model called tool: {tool_call.name}({tool_call.function['arguments']})")

    tool_output = await provider.execute_tool(tool_call, {"add_numbers": add_numbers})
    print(f"Tool output: {tool_output}")

    follow_up = [
        {"role": "user", "content": question},
        main_response.original,
        tool_output,
    ]
    final = await provider.acomplete(model=MODEL, messages=follow_up, stream=True)
    final_text = "".join([chunk async for chunk in final.stream_content()])
    print(f"Final answer: {final_text}")

    expected = str(add_numbers(482193, 917364))
    if expected not in final_text.replace(",", ""):
        print(f"FAIL: expected {expected!r} in the final answer")
        return 1
    print("PASS: tool calling round-trip works")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
