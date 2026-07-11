import tomllib

from autodev.config import find_project_root, load_config, write_project


def test_defaults_load(tmp_path):
    cfg = load_config(start=tmp_path)
    assert cfg.model_string == "ollama:qwen2.5-coder:7b"
    assert cfg.sandbox["network"] == "none"


def test_project_overrides_global(tmp_path):
    write_project(tmp_path, {"model": {"default": "groq:llama-3.3-70b-versatile"}})
    cfg = load_config(start=tmp_path)
    assert cfg.model_string == "groq:llama-3.3-70b-versatile"
    # untouched defaults survive the merge
    assert cfg.sandbox["network"] == "none"


def test_cli_overrides_win(tmp_path):
    write_project(tmp_path, {"sandbox": {"network": "full"}})
    cfg = load_config(start=tmp_path, overrides={"sandbox": {"network": "none"}})
    assert cfg.sandbox["network"] == "none"


def test_find_project_root_prefers_marker(tmp_path):
    (tmp_path / ".autodev.toml").write_text("")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    assert find_project_root(sub) == tmp_path


def test_write_project_merges(tmp_path):
    write_project(tmp_path, {"model": {"default": "x:y"}})
    write_project(tmp_path, {"sandbox": {"network": "full"}})
    data = tomllib.loads((tmp_path / ".autodev.toml").read_text())
    assert data["model"]["default"] == "x:y"
    assert data["sandbox"]["network"] == "full"


def test_dotted_get(tmp_path):
    cfg = load_config(start=tmp_path)
    assert cfg.get("policy.deny") is not None
    assert cfg.get("nope.missing", "d") == "d"
