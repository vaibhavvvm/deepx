"""llama.cpp provider: raw GGUF served by ``llama-server`` (OpenAI-shaped API).

llama.cpp's server speaks the OpenAI ``/v1/chat/completions`` shape, so we reuse
``langchain-openai``'s ``ChatOpenAI`` pointed at the local server. The API key is
ignored by the server but ``ChatOpenAI`` requires a non-empty value.
"""

from __future__ import annotations

import os

from openlocal.providers.base import Provider, ProviderSpec, register_provider

DEFAULT_BASE_URL = os.environ.get("LLAMACPP_BASE_URL", "http://localhost:8080/v1")


class LlamaCppProvider(Provider):
    name = "llamacpp"
    requires_api_key = False
    is_local = True

    def __init__(self, base_url: str = DEFAULT_BASE_URL):
        self.base_url = base_url

    def build_spec(self, model: str) -> ProviderSpec:
        return ProviderSpec(
            name=self.name,
            model=model,
            requires_api_key=False,
            is_local=True,
            # llama-server serves a single loaded GGUF; the context window is a
            # server launch flag we can't introspect, so assume a conservative
            # value the user can override in config.
            context_window=int(os.environ.get("LLAMACPP_CONTEXT", "8192")),
            supports_tool_calling=True,
            extra={"base_url": self.base_url},
        )

    def resolve(self, spec: ProviderSpec):
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ImportError(
                "llama.cpp support needs the 'llamacpp' extra: "
                "pipx install 'openlocal-cli[llamacpp]'"
            ) from exc

        return ChatOpenAI(
            base_url=spec.extra.get("base_url", self.base_url),
            api_key="not-needed",  # llama-server ignores this
            model=spec.model,
            temperature=0,
        )

    def health(self) -> tuple[bool, str]:
        try:
            import httpx

            # /v1/models is served by llama-server.
            resp = httpx.get(f"{self.base_url}/models", timeout=3.0)
            resp.raise_for_status()
            return True, f"llama-server reachable at {self.base_url}"
        except Exception as exc:  # pragma: no cover - network dependent
            return (
                False,
                f"llama-server not reachable at {self.base_url}: {exc}. "
                "Start it with: llama-server -m model.gguf --port 8080",
            )

    def list_models(self) -> list[str]:
        try:
            import httpx

            resp = httpx.get(f"{self.base_url}/models", timeout=3.0)
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
        except Exception:  # pragma: no cover - network dependent
            return []


register_provider(LlamaCppProvider())
