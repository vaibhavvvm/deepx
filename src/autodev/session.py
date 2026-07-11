"""Session lifecycle and resumability.

Each ``autodev start`` gets a UUID, a LangGraph SQLite checkpointer at
``.autodev/sessions/<uuid>.db``, and a small JSON metadata record. Because
checkpoints are addressable this also lays the groundwork for a future
``autodev rewind`` (blueprint section 10) -- we keep the thread id stable and
never mutate history in place.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SESSIONS_DIRNAME = ".autodev/sessions"


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SessionMeta:
    id: str
    model_string: str
    status: str = "running"  # running | done | failed | paused
    prompt: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    image: str = ""
    network: str = "none"
    container_name: str = ""

    def touch(self, status: str | None = None) -> None:
        if status:
            self.status = status
        self.updated_at = _now()


def sessions_dir(project_root: Path) -> Path:
    d = project_root / SESSIONS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(project_root: Path, session_id: str) -> Path:
    return sessions_dir(project_root) / f"{session_id}.json"


def _db_path(project_root: Path, session_id: str) -> Path:
    return sessions_dir(project_root) / f"{session_id}.db"


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def save_meta(project_root: Path, meta: SessionMeta) -> None:
    meta.touch()
    _meta_path(project_root, meta.id).write_text(
        json.dumps(asdict(meta), indent=2), encoding="utf-8"
    )


def load_meta(project_root: Path, session_id: str) -> SessionMeta | None:
    path = _meta_path(project_root, session_id)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SessionMeta(**data)


def list_sessions(project_root: Path) -> list[SessionMeta]:
    d = project_root / SESSIONS_DIRNAME
    if not d.exists():
        return []
    metas: list[SessionMeta] = []
    for path in sorted(d.glob("*.json")):
        try:
            metas.append(SessionMeta(**json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    metas.sort(key=lambda m: m.created_at, reverse=True)
    return metas


@contextmanager
def open_checkpointer(project_root: Path, session_id: str):
    """Yield a SQLite checkpointer bound to this session's db file."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    db = _db_path(project_root, session_id)
    db.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(db)) as saver:
        yield saver


def thread_config(session_id: str) -> dict:
    """LangGraph config selecting this session's checkpoint thread."""
    return {"configurable": {"thread_id": session_id}}


def ensure_gitignore(project_root: Path) -> None:
    """Make sure ``.autodev/`` (state, secrets-adjacent) is git-ignored."""
    gitignore = project_root / ".gitignore"
    line = ".autodev/"
    existing = ""
    if gitignore.exists():
        existing = gitignore.read_text(encoding="utf-8")
        if any(ln.strip() == line for ln in existing.splitlines()):
            return
    prefix = "" if existing.endswith("\n") or not existing else "\n"
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{line}\n")
