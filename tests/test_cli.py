from typer.testing import CliRunner

from autodev.cli import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "auto-dev" in result.stdout


def test_help():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.stdout
    assert "start" in result.stdout


def test_config_set_and_get(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r1 = runner.invoke(app, ["config", "set", "model.default", "ollama:foo:7b"])
    assert r1.exit_code == 0
    r2 = runner.invoke(app, ["config", "get", "model.default"])
    assert r2.exit_code == 0
    assert "ollama:foo:7b" in r2.stdout


def test_config_get_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(app, ["config", "get", "nope.nope"])
    assert r.exit_code == 1
