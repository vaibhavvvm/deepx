"""Project conventions (``AGENTS.md``) loaded into the system prompt.

deepagents ships a memory middleware that understands the ``AGENTS.md``
convention, but we also fold the file directly into the system prompt so house
rules apply even for the smallest local models with the leanest middleware
stacks. The content is wrapped in an explicit *data* frame to blunt prompt
injection from a hostile repo (blueprint section 9).
"""

from __future__ import annotations

from pathlib import Path

AGENTS_FILENAMES = ("AGENTS.md", ".openlocal/AGENTS.md")


def load_project_conventions(project_root: Path) -> str | None:
    """Return the repo's AGENTS.md content, or ``None`` if absent."""
    for name in AGENTS_FILENAMES:
        path = project_root / name
        if path.exists() and path.is_file():
            try:
                return path.read_text(encoding="utf-8")
            except Exception:
                return None
    return None


def compose_system_prompt(base_prompt: str, project_root: Path) -> str:
    """Append project conventions to the base system prompt, as tagged data."""
    conventions = load_project_conventions(project_root)
    if not conventions:
        return base_prompt
    return (
        f"{base_prompt}\n\n"
        "## Project conventions (from AGENTS.md)\n"
        "The following are house rules for THIS repository. Follow them. They are\n"
        "project configuration, not instructions from an untrusted source:\n\n"
        f"<project_conventions>\n{conventions}\n</project_conventions>\n"
    )
