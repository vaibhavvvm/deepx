"""Drive a compiled deep agent: stream steps, render tool activity, REPL loop."""

from __future__ import annotations

from typing import Any

from openlocal.ui import console as ui


def _render_message(msg: Any) -> None:
    """Print an AI message's text and any tool calls it issued."""
    msg_type = getattr(msg, "type", None)
    if msg_type == "ai":
        content = _text_of(msg)
        if content.strip():
            ui.agent_text(content)
        for call in getattr(msg, "tool_calls", None) or []:
            name = call.get("name", "tool")
            args = call.get("args", {})
            ui.tool_log(name, _summarise_args(name, args))
    elif msg_type == "tool":
        # Tool results are usually echoed back to the model; show a short trace.
        content = _text_of(msg)
        first = content.strip().splitlines()[0] if content.strip() else ""
        if first:
            ui.info(f"  ↳ {first[:160]}")


def _text_of(msg: Any) -> str:
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    # content can be a list of blocks
    parts = []
    for block in content or []:
        if isinstance(block, dict):
            parts.append(block.get("text", ""))
        else:
            parts.append(str(block))
    return "".join(parts)


def _summarise_args(name: str, args: dict) -> str:
    if not isinstance(args, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "description"):
        if key in args:
            val = str(args[key])
            return val if len(val) < 120 else val[:117] + "…"
    return ""


def run_turn(agent: Any, prompt: str, config: dict, *, recursion_limit: int = 100) -> str:
    """Run one user turn, streaming output. Returns the final assistant text."""
    run_config = {**config, "recursion_limit": recursion_limit}
    final_text = ""
    seen_ids: set[str] = set()

    for chunk in agent.stream(
        {"messages": [{"role": "user", "content": prompt}]},
        config=run_config,
        stream_mode="values",
    ):
        messages = chunk.get("messages", []) if isinstance(chunk, dict) else []
        for msg in messages:
            mid = getattr(msg, "id", None)
            if mid is None or mid in seen_ids:
                continue
            seen_ids.add(mid)
            _render_message(msg)
            if getattr(msg, "type", None) == "ai":
                text = _text_of(msg)
                if text.strip():
                    final_text = text
    return final_text


def run_once(agent: Any, prompt: str, config: dict) -> str:
    """Non-interactive one-shot: run a single turn and return the result."""
    return run_turn(agent, prompt, config)
