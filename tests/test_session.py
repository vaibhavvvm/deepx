from openlocal.session import (
    SessionMeta,
    ensure_gitignore,
    list_sessions,
    load_meta,
    new_session_id,
    save_meta,
)


def test_save_and_load_roundtrip(tmp_path):
    meta = SessionMeta(id=new_session_id(), model_string="ollama:qwen2.5-coder:7b", prompt="hi")
    save_meta(tmp_path, meta)
    loaded = load_meta(tmp_path, meta.id)
    assert loaded is not None
    assert loaded.model_string == "ollama:qwen2.5-coder:7b"
    assert loaded.prompt == "hi"


def test_list_sessions_sorted(tmp_path):
    a = SessionMeta(id="aaa", model_string="m", created_at="2024-01-01T00:00:00")
    b = SessionMeta(id="bbb", model_string="m", created_at="2025-01-01T00:00:00")
    save_meta(tmp_path, a)
    save_meta(tmp_path, b)
    ids = [m.id for m in list_sessions(tmp_path)]
    assert ids[0] == "bbb"  # newest first


def test_missing_session_returns_none(tmp_path):
    assert load_meta(tmp_path, "nope") is None


def test_status_touch():
    m = SessionMeta(id="x", model_string="m")
    old = m.updated_at
    m.touch("done")
    assert m.status == "done"
    assert m.updated_at >= old


def test_ensure_gitignore_adds_line(tmp_path):
    ensure_gitignore(tmp_path)
    content = (tmp_path / ".gitignore").read_text()
    assert ".openlocal/" in content
    # idempotent
    ensure_gitignore(tmp_path)
    assert content.count(".openlocal/") == 1
