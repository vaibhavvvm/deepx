"""Structured retry for unreliable tool-calling on small local models.

When a 4B-8B model emits a malformed tool call, LangChain surfaces it on the
``AIMessage.invalid_tool_calls`` list with a parse error. Instead of letting
that dead-end the turn, we re-prompt once with the exact error and a reminder to
emit valid JSON, then take the retried response (blueprint 4.5).
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import HumanMessage

from autodev.ui import console as ui


def _invalid_tool_calls(ai_message) -> list:
    return list(getattr(ai_message, "invalid_tool_calls", None) or [])


class ToolCallRetryMiddleware:
    """Re-prompt once when the model emits an unparseable tool call.

    Kept dependency-light (duck-typed on the middleware protocol) so it works
    across deepagents/langchain middleware versions.
    """

    name = "autodev_toolcall_retry"

    def __init__(self, max_retries: int = 1):
        self.max_retries = max_retries

    def wrap_model_call(self, request, handler: Callable):  # type: ignore[override]
        response = handler(request)
        attempts = 0
        while attempts < self.max_retries:
            ai = _first_message(response)
            bad = _invalid_tool_calls(ai)
            if not bad:
                return response
            attempts += 1
            errors = "; ".join(
                f"{c.get('name', '?')}: {c.get('error', 'parse error')}" for c in bad
            )
            ui.warn(f"malformed tool call — retrying with schema error ({errors})")
            nudge = HumanMessage(
                content=(
                    "Your previous tool call could not be parsed:\n"
                    f"{errors}\n\n"
                    "Re-issue the tool call with strictly valid JSON arguments "
                    "matching the tool's schema. Do not add prose around it."
                )
            )
            retry_request = request.override(messages=[*request.messages, ai, nudge])
            response = handler(retry_request)
        return response


def _first_message(response):
    result = getattr(response, "result", None)
    if result:
        return result[0]
    # Some handlers may return an AIMessage directly.
    return response
