"""Repair Loop Middleware: MiMo-Code 'Trajectory Reflection' pattern.

When the model tries to declare a task complete but its last sandbox command
failed, we intercept the response and force a structured Chain-of-Thought
*reflection* before the model is allowed to attempt another code change.

This prevents the classic pattern:
  1. Test fails.
  2. Model edits file with a *guess*.
  3. Test fails again.
  4. Model makes the *same guess again* because it forgot what it tried.

By forcing the model to write to the `scratchpad` first (recording what it
tried and why it failed), we inject working memory directly into the
LangGraph context, stopping the panic-fix cycle.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import HumanMessage
from langchain.agents.middleware import AgentMiddleware


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_message(response):
    result = getattr(response, "result", None)
    if result:
        return result[0]
    return response


def _last_execute_result(messages) -> tuple[bool, str]:
    """Scan backwards for the most recent `execute` tool result.

    Returns (failed: bool, output: str).
    A command is considered failed if:
     - Its output contains a non-zero exit notice (e.g. 'exit 1')
     - It does NOT contain 'exit 0'
     - OR it contains 'TIMED OUT' / 'REFUSED'
    """
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", None)
        msg_name = getattr(msg, "name", None)
        # LangChain represents tool results as type="tool"
        if msg_type == "tool" and msg_name == "execute":
            content = str(getattr(msg, "content", ""))
            failed = (
                "exit 0" not in content
                and (
                    "exit " in content
                    or "TIMED OUT" in content
                    or "REFUSED" in content
                    or "error" in content.lower()
                )
            )
            return failed, content
        # Stop scanning once we pass an AI message that had tool calls
        # (the one that triggered the execute) — don't scan old history
        if msg_type == "ai" and getattr(msg, "tool_calls", None):
            break

    return False, ""


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

class RepairLoopMiddleware(AgentMiddleware):
    """Intercepts premature 'task complete' when the last execute failed.

    MiMo-Code 'Trajectory Reflection': before attempting a fix the model
    MUST write a structured reflection to its scratchpad:
      1. What did it just try?
      2. Why did it result in this specific error?
      3. New hypothesis — and how it differs from the last attempt.

    This injects Chain-of-Thought reasoning into the context window and
    prevents the model from repeating the same broken fix.
    """

    name = "openlocal_repair_loop"

    def __init__(self):
        super().__init__()

    def wrap_model_call(self, request, handler: Callable):
        response = handler(request)

        # Only intercept when the model is ending its turn (no tool calls)
        ai_msg = _first_message(response)
        if getattr(ai_msg, "tool_calls", None):
            return response

        failed, error_output = _last_execute_result(request.messages)
        if not failed:
            return response

        # --- Trajectory Reflection nudge (MiMo-Code pattern) ---
        reflection_prompt = HumanMessage(
            content=(
                "The test/build/command failed with the following output:\n"
                "```\n"
                f"{error_output[:2000]}\n"
                "```\n\n"
                "STOP. Do not attempt a fix yet.\n\n"
                "You MUST first call your `scratchpad` tool and record:\n"
                "1. **What you just tried** — the exact edit or command.\n"
                "2. **Why it caused this specific error** — trace the failure.\n"
                "3. **Your new hypothesis** — what is different about this next "
                "attempt versus the last one?\n\n"
                "Only after writing the scratchpad entry may you proceed to fix "
                "the code. Do not repeat the same approach."
            )
        )

        from openlocal.ui import console as ui
        ui.warn("repair-loop: last command failed — forcing trajectory reflection")

        retry_request = request.override(
            messages=[*request.messages, ai_msg, reflection_prompt]
        )
        return handler(retry_request)
