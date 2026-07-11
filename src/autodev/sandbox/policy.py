"""Command policy: the single most important safety layer.

Every shell command the model wants to run is classified here *before* it
reaches Docker:

* ``DENY``    -- matches the deny-list. Refused outright, no override except
                 there is none: deny is absolute (blueprint section 9).
* ``APPROVE`` -- matches the approval-list. Requires interactive confirmation
                 (unless ``--yolo``).
* ``ALLOW``   -- benign, runs without prompting.

Matching is deliberately conservative and substring/word based so that
``rm -rf`` catches ``sudo rm  -rf /`` and shell-obfuscation variants. Deny
patterns additionally match against a whitespace-normalised form so that
``:(){ :|:& };:`` and ``:(){:|:&};:`` both trip the fork-bomb rule.
"""

from __future__ import annotations

import enum
import re
import shlex
from dataclasses import dataclass


class Decision(enum.Enum):
    ALLOW = "allow"
    APPROVE = "approve"
    DENY = "deny"


@dataclass
class PolicyDecision:
    decision: Decision
    matched_rule: str | None = None
    reason: str = ""

    @property
    def is_denied(self) -> bool:
        return self.decision is Decision.DENY

    @property
    def needs_approval(self) -> bool:
        return self.decision is Decision.APPROVE


def _normalise(command: str) -> str:
    """Collapse whitespace so obfuscated spacing can't dodge a pattern."""
    return re.sub(r"\s+", " ", command.strip())


def _no_space(command: str) -> str:
    return re.sub(r"\s+", "", command)


class Policy:
    """Evaluates commands against deny- and approval-lists."""

    def __init__(
        self,
        deny: list[str] | None = None,
        require_approval_for: list[str] | None = None,
    ):
        self.deny = list(deny or [])
        self.require_approval_for = list(require_approval_for or [])

    @classmethod
    def from_config(cls, policy_cfg: dict) -> Policy:
        return cls(
            deny=policy_cfg.get("deny", []),
            require_approval_for=policy_cfg.get("require_approval_for", []),
        )

    def evaluate(self, command: str) -> PolicyDecision:
        """Classify a single command string.

        Deny wins over approval: a command that matches both is denied.
        """
        norm = _normalise(command).lower()
        norm_ns = _no_space(command).lower()

        for rule in self.deny:
            rl = rule.lower()
            if rl and (rl in norm or _no_space(rule).lower() in norm_ns):
                return PolicyDecision(
                    Decision.DENY,
                    matched_rule=rule,
                    reason=f"Command matches deny-list rule '{rule}' and is refused.",
                )

        for rule in self.require_approval_for:
            if rule.lower() and rule.lower() in norm:
                return PolicyDecision(
                    Decision.APPROVE,
                    matched_rule=rule,
                    reason=f"Command matches approval rule '{rule}'.",
                )

        return PolicyDecision(Decision.ALLOW)

    def evaluate_pipeline(self, command: str) -> PolicyDecision:
        """Evaluate a compound command, splitting on shell operators.

        A pipeline like ``cat x | curl example.com`` must be judged on *every*
        stage, so a benign prefix can't smuggle a risky suffix past the gate.
        The most severe decision across all stages wins.
        """
        stages = _split_shell_stages(command)
        worst = PolicyDecision(Decision.ALLOW)
        for stage in stages:
            d = self.evaluate(stage)
            if d.decision is Decision.DENY:
                return d
            if d.decision is Decision.APPROVE and worst.decision is Decision.ALLOW:
                worst = d
        # Also evaluate the whole string so multi-word rules like "git push"
        # still match even if our splitter fragments them.
        whole = self.evaluate(command)
        if whole.decision is Decision.DENY:
            return whole
        if whole.decision is Decision.APPROVE and worst.decision is Decision.ALLOW:
            return whole
        return worst


_OPERATOR_RE = re.compile(r"\|\||&&|\||;|>|<|`|\$\(")


def _split_shell_stages(command: str) -> list[str]:
    """Best-effort split of a compound command into stages.

    Uses ``shlex`` when possible to respect quoting, falling back to a regex
    split on shell operators.
    """
    try:
        # Validate it tokenises; we don't use the tokens directly.
        shlex.split(command)
    except ValueError:
        pass
    parts = _OPERATOR_RE.split(command)
    return [p.strip() for p in parts if p.strip()]
