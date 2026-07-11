import pytest

from autodev.providers.base import (
    ProviderSpec,
    build_spec,
    get_provider,
    list_providers,
    parse_model_string,
)


def test_parse_model_string_splits_first_colon():
    assert parse_model_string("ollama:qwen2.5-coder:7b") == ("ollama", "qwen2.5-coder:7b")
    assert parse_model_string("groq:llama-3.3-70b-versatile") == (
        "groq",
        "llama-3.3-70b-versatile",
    )


def test_parse_model_string_rejects_missing_colon():
    with pytest.raises(ValueError):
        parse_model_string("ollama")


def test_ollama_spec_local_no_key():
    spec = build_spec("ollama:qwen2.5-coder:7b")
    assert spec.is_local
    assert not spec.requires_api_key
    assert spec.supports_tool_calling
    assert spec.model_string == "ollama:qwen2.5-coder:7b"
    assert spec.label == "local"


def test_ollama_unknown_family_flags_toolcalling_off():
    spec = build_spec("ollama:some-obscure-1b")
    assert spec.supports_tool_calling is False


def test_groq_spec_cloud_requires_key():
    spec = build_spec("groq:llama-3.3-70b-versatile")
    assert not spec.is_local
    assert spec.requires_api_key
    assert spec.label == "cloud: groq"


def test_registry_has_builtins():
    names = {p.name for p in list_providers()}
    assert {"ollama", "llamacpp", "groq"} <= names


def test_get_unknown_provider_errors():
    with pytest.raises(KeyError):
        get_provider("does-not-exist")


def test_provider_spec_defaults():
    s = ProviderSpec(name="x", model="y", requires_api_key=False, is_local=True)
    assert s.context_window == 8192
    assert s.model_string == "x:y"
