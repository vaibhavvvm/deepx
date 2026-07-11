"""Opt-in, local-only usage counters. Off by default. Never auto-uploaded.

Exactly what is collected (and nothing else): a per-command invocation count and
a last-used timestamp, stored in ``~/.autodev/telemetry.json``. No prompts, no
file contents, no model output, no network. This mirrors the promise in
SECURITY.md -- the code is the spec.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

TELEMETRY_PATH = Path.home() / ".autodev" / "telemetry.json"


def _load() -> dict:
    if not TELEMETRY_PATH.exists():
        return {"commands": {}}
    try:
        return json.loads(TELEMETRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"commands": {}}


def record_command(name: str, *, enabled: bool) -> None:
    """Increment a command's counter -- only when telemetry is enabled."""
    if not enabled:
        return
    try:
        data = _load()
        cmds = data.setdefault("commands", {})
        entry = cmds.setdefault(name, {"count": 0, "last": ""})
        entry["count"] += 1
        entry["last"] = datetime.now(UTC).isoformat()
        TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
        TELEMETRY_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        # Telemetry must never break the tool.
        pass


def summary() -> dict:
    """Return the collected counters for display (``autodev config telemetry``)."""
    return _load().get("commands", {})
