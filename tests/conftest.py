"""Test isolation: never read the developer's real global config or keyring."""

from pathlib import Path

import pytest

import openlocal.config as config_mod


@pytest.fixture(autouse=True)
def isolated_global_config(tmp_path_factory, monkeypatch):
    """Point the global config at an empty temp file for every test."""
    fake_global = tmp_path_factory.mktemp("global") / "config.toml"
    monkeypatch.setattr(config_mod, "GLOBAL_CONFIG_PATH", Path(fake_global))
    yield
