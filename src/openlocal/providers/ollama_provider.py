"""Ollama provider: local models managed by the Ollama daemon."""

from __future__ import annotations

import os

from openlocal.providers.base import Provider, ProviderSpec, register_provider

DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")

# Model families known to be tuned for reliable tool-calling. Small local
# models outside this list get flagged so the harness can reduce its tool
# surface (see agent/build.py and blueprint 4.5).
_TOOL_CALLING_FAMILIES = (
    "qwen2.5-coder",
    "qwen2.5",
    "qwen3",
    "llama3.1",
    "llama3.2",
    "llama3.3",
    "mistral",
    "mixtral",
    "firefunction",
    "command-r",
    "gpt-oss",
    "hermes",
)

# Best-effort context windows by family; falls back to a safe small default.
_CONTEXT_HINTS = {
    "qwen2.5-coder": 32768,
    "qwen2.5": 32768,
    "qwen3": 32768,
    "llama3.1": 131072,
    "llama3.2": 131072,
    "llama3.3": 131072,
    "gpt-oss": 131072,
    "mistral": 32768,
    "mixtral": 32768,
    "gemma3": 8192,
}


def _family(model: str) -> str:
    return model.split(":", 1)[0].lower()


class OllamaProvider(Provider):
    name = "ollama"
    requires_api_key = False
    is_local = True

    def __init__(self, host: str = DEFAULT_HOST):
        self.host = host

    def build_spec(self, model: str) -> ProviderSpec:
        fam = _family(model)
        supports_tools = any(fam.startswith(f) for f in _TOOL_CALLING_FAMILIES)
        ctx = next((v for k, v in _CONTEXT_HINTS.items() if fam.startswith(k)), 8192)
        return ProviderSpec(
            name=self.name,
            model=model,
            requires_api_key=False,
            is_local=True,
            context_window=ctx,
            supports_tool_calling=supports_tools,
            extra={"base_url": self.host},
        )

    def resolve(self, spec: ProviderSpec):
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=spec.model,
            base_url=spec.extra.get("base_url", self.host),
            # num_ctx lets small local models use their full window when known.
            num_ctx=spec.context_window,
            temperature=0,
        )

    def health(self) -> tuple[bool, str]:
        try:
            import httpx

            resp = httpx.get(f"{self.host}/api/tags", timeout=3.0)
            resp.raise_for_status()
            n = len(resp.json().get("models", []))
            return True, f"Ollama reachable at {self.host} ({n} model(s) installed)"
        except Exception as exc:  # pragma: no cover - network dependent
            return False, f"Ollama not reachable at {self.host}: {exc}"

    def list_models(self) -> list[str]:
        try:
            import httpx

            resp = httpx.get(f"{self.host}/api/tags", timeout=3.0)
            resp.raise_for_status()
            return [m["name"] for m in resp.json().get("models", [])]
        except Exception:  # pragma: no cover - network dependent
            return []

    def pull(self, model: str) -> None:
        """Proxy to the Ollama pull endpoint (streaming)."""
        import httpx

        with httpx.stream(
            "POST",
            f"{self.host}/api/pull",
            json={"model": model},
            timeout=None,
        ) as resp:
            resp.raise_for_status()
            for _ in resp.iter_lines():
                pass


register_provider(OllamaProvider())
