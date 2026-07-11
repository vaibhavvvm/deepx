"""Provider layer: pluggable model backends (Ollama, llama.cpp, Groq)."""

from autodev.providers.base import (
    ProviderSpec,
    build_spec,
    get_provider,
    list_providers,
    parse_model_string,
    resolve_model,
)

__all__ = [
    "ProviderSpec",
    "build_spec",
    "get_provider",
    "list_providers",
    "parse_model_string",
    "resolve_model",
]
