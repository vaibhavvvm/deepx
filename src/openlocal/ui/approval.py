"""Interactive approval prompts for policy-gated commands.

The approval gate is the single most important safety feature and must never be
bypassable except by the explicit ``--yolo`` flag (blueprint 8.3). This module
builds an :data:`ApprovalCallback` compatible with ``DockerSandboxBackend``.
"""

from __future__ import annotations

from rich.panel import Panel
from rich.prompt import Prompt
from rich.syntax import Syntax

from openlocal.sandbox.policy import PolicyDecision
from openlocal.ui.console import console


class ApprovalController:
    """Stateful approval callback supporting 'always allow this session'.

    A user can answer ``a`` to allow a matched rule for the rest of the session,
    so repetitive confirmations (e.g. many ``pip install`` calls) don't grind
    the workflow to a halt while still requiring an initial explicit yes.
    """

    def __init__(self, yolo: bool = False):
        self.yolo = yolo
        self._session_allowed_rules: set[str] = set()

    def __call__(self, command: str, decision: PolicyDecision) -> bool:
        if self.yolo:
            return True
        rule = decision.matched_rule or command
        if rule in self._session_allowed_rules:
            console.print(f"[dim]auto-approved (session): {rule}[/dim]")
            return True

        console.print(
            Panel(
                Syntax(command, "bash", theme="ansi_dark", word_wrap=True),
                title=f"[yellow]approval required[/yellow] — rule '{decision.matched_rule}'",
                border_style="yellow",
            )
        )
        choice = Prompt.ask(
            "Run this command?",
            choices=["y", "n", "a"],
            default="n",
        )
        if choice == "a":
            self._session_allowed_rules.add(rule)
            return True
        return choice == "y"


def auto_approve(command: str, decision: PolicyDecision) -> bool:
    """Non-interactive callback for ``--yes``/CI: approve approval-listed items.

    Deny-listed commands are still refused inside the backend regardless.
    """
    return True
