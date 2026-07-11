"""Middleware that redacts secrets from cloud-bound model requests.

Wired only when the active provider is a *cloud* provider. It scans outgoing
message content just before the model call and redacts secret-shaped strings,
reporting findings to the console. For local providers this middleware is not
installed at all, so there is zero overhead and the UI distinction (nothing
leaves the machine) is real, not cosmetic (blueprint 7.5 / section 9).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from autodev.sandbox.secret_scan import scan
from autodev.ui import console as ui


def _redact_content(content: Any) -> tuple[Any, list]:
    """Redact a message ``content`` (str or list-of-blocks). Returns findings."""
    findings: list = []
    if isinstance(content, str):
        result = scan(content)
        findings.extend(result.findings)
        return result.redacted_text, findings
    if isinstance(content, list):
        new_blocks = []
        for block in content:
            if isinstance(block, dict) and "text" in block:
                result = scan(block["text"])
                findings.extend(result.findings)
                new_blocks.append({**block, "text": result.redacted_text})
            else:
                new_blocks.append(block)
        return new_blocks, findings
    return content, findings


class CloudRedactionMiddleware(AgentMiddleware):
    """Scrub secrets from messages before they are sent to a cloud model."""

    name = "autodev_cloud_redaction"

    def __init__(self, announce: bool = True):
        super().__init__()
        self.announce = announce

    def wrap_model_call(self, request, handler: Callable):  # type: ignore[override]
        redacted_messages = []
        all_findings: list = []
        for msg in request.messages:
            content = getattr(msg, "content", None)
            if content is None:
                redacted_messages.append(msg)
                continue
            new_content, findings = _redact_content(content)
            all_findings.extend(findings)
            if findings:
                msg = msg.model_copy(update={"content": new_content})
            redacted_messages.append(msg)

        if all_findings and self.announce:
            ui.warn(
                f"redacted {len(all_findings)} secret-shaped value(s) before "
                "sending to the cloud provider"
            )
            ui.secret_findings(all_findings)

        new_request = request.override(messages=redacted_messages)
        return handler(new_request)
