"""Shared test doubles — not collected by pytest (module name doesn't match
test_*.py / *_test.py), imported directly by integration + PB tests.

Per sdk/how-to/write-tests.md's FakeLLM pattern, extended with bind_tools()
(required for the AutonomousBaseGraph loop) and a scripted response queue so
a test can drive a deterministic sequence of tool calls across iterations —
no real LLM call, no network.
"""

from __future__ import annotations

from typing import Any


class ScriptedFakeLLM:
    """A fake BaseLLM that returns a pre-scripted sequence of canonical
    responses ({"content": str, "tool_calls": list, ...}), one per
    .complete() call. The last script entry repeats if .complete() is called
    more times than the script has entries (safety net against infinite
    loops in a misbehaving test, not a production concern)."""

    def __init__(self, script: list[dict[str, Any]]):
        assert script, "script must have at least one entry"
        self._script = script
        self.call_count = 0
        self.prompts: list[str] = []

    def complete(self, prompt: str, **kwargs) -> dict:
        self.prompts.append(prompt)
        idx = min(self.call_count, len(self._script) - 1)
        self.call_count += 1
        return self._script[idx]

    def stream(self, messages: list):  # pragma: no cover - not exercised
        raise NotImplementedError("ScriptedFakeLLM does not support streaming")

    def bind_tools(self, tools: list) -> "ScriptedFakeLLM":
        return self


def tool_call_step(name: str, args: dict, content: str = "") -> dict:
    return {"content": content, "tool_calls": [{"name": name, "args": args}]}


def final_step(content: str = "done") -> dict:
    return {"content": content, "tool_calls": []}
