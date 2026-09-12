"""Smoke test: verify tool calling works end-to-end against a live Codex login.

Usage:
    .venv/bin/python scripts/test_tool_calling.py [path/to/auth.json]

Requires a real auth.json (default: ./auth.json) produced by `llm-async-codex login`.

The installed llm-async version only surfaces text deltas from streaming
responses, not tool-call events, so the first turn reads the raw SSE stream
directly to pull out the function_call item.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from llm_async.models import Tool
from llm_async.models.tool_call import ToolCall
from llm_async.utils.http import stream_json

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


async def request_tool_call(provider: CodexProvider, question: str) -> ToolCall:
    payload = {
        "model": MODEL,
        "input": provider._messages_to_input([{"role": "user", "content": question}]),
        "tools": provider._format_tools([ADD_NUMBERS_TOOL]),
        "tool_choice": "required",
        "stream": True,
        "store": False,
    }
    headers = provider._default_headers()

    async for chunk in stream_json(
        provider.client, f"{provider.base_url}/responses", payload, headers
    ):
        if chunk.get("type") != "response.output_item.done":
            continue
        item = chunk["item"]
        if item.get("type") == "function_call":
            return ToolCall.from_responses_api_function_call(
                fc_id=item["id"],
                call_id=item["call_id"],
                name=item["name"],
                arguments=item["arguments"],
            )
    raise RuntimeError("model did not call any tool")


async def main() -> int:
    auth_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("auth.json")
    provider = CodexProvider(load_credentials(auth_path))

    question = (
        "What is 482193 + 917364? Use the add_numbers tool to compute it, "
        "then tell me the result."
    )
    tool_call = await request_tool_call(provider, question)
    print(f"Model called tool: {tool_call.name}({tool_call.function['arguments']})")

    tool_output = await provider.execute_tool(tool_call, {"add_numbers": add_numbers})
    print(f"Tool output: {tool_output}")

    follow_up = [
        {"role": "user", "content": question},
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tool_call.id,
                    "input": tool_call.input,
                    "function": tool_call.function,
                }
            ],
        },
        tool_output,
    ]
    response = await provider.acomplete(model=MODEL, messages=follow_up, stream=True)
    final_text = "".join([chunk async for chunk in response.stream_content()])
    print(f"Final answer: {final_text}")

    expected = str(add_numbers(482193, 917364))
    if expected not in final_text.replace(",", ""):
        print(f"FAIL: expected {expected!r} in the final answer")
        return 1
    print("PASS: tool calling round-trip works")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
