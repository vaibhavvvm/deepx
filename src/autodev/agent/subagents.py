"""Subagent specifications (deepagents' ``task`` delegation).

Splitting planning / coding / testing / reviewing into focused subagents keeps
each context window tight and lets the user run a different (often cheaper)
model per role. Per-role model overrides come from
``.autodev.toml [subagents.models]`` (blueprint 8.1).
"""

from __future__ import annotations

from deepagents import SubAgent

from autodev.agent.build import load_prompt


def build_subagents(
    role_models: dict[str, str] | None = None,
    *,
    include: set[str] | None = None,
) -> list[SubAgent]:
    """Construct subagent specs, applying per-role model overrides.

    ``include`` optionally restricts which roles are built (used to trim the
    tool surface for small local models -- blueprint 4.5).
    """
    role_models = role_models or {}
    include = include or {"planner", "coder", "tester", "reviewer"}

    specs: dict[str, SubAgent] = {
        "planner": SubAgent(
            name="planner",
            description=(
                "Decompose a coding task into an ordered, verifiable plan. "
                "Read-only; no code changes. Delegate here first for anything "
                "multi-step or ambiguous."
            ),
            system_prompt=load_prompt("planner.md"),
        ),
        "coder": SubAgent(
            name="coder",
            description=(
                "Implement a single well-scoped code change and verify it. Use "
                "for the actual editing work once a plan exists."
            ),
            system_prompt=load_prompt("coder.md"),
        ),
        "tester": SubAgent(
            name="tester",
            description=(
                "Run the project's test suite and report pass/fail with failing "
                "test names. Does not modify source."
            ),
            system_prompt=load_prompt("tester.md"),
        ),
        "reviewer": SubAgent(
            name="reviewer",
            description=(
                "Read-only review of a proposed diff for correctness and risk. "
                "Flags bugs, security issues, and destructive operations."
            ),
            system_prompt=load_prompt("reviewer.md"),
        ),
    }

    result: list[SubAgent] = []
    for role, spec in specs.items():
        if role not in include:
            continue
        if role in role_models:
            spec = {**spec, "model": role_models[role]}  # type: ignore[assignment]
        result.append(spec)
    return result
