"""Groq provider: fast hosted inference, strictly bring-your-own-key.

This is the only built-in provider that sends code off the machine, so it is
where the secret scan and the cloud banner become non-negotiable. The API key is
resolved from (in priority order): the OS keyring, then the ``GROQ_API_KEY``
environment variable. It is never read from or written to project config.
"""

from __future__ import annotations

import os

from autodev.providers.base import Provider, ProviderSpec, register_provider

KEYRING_SERVICE = "auto-dev-cli"
KEYRING_USERNAME = "groq_api_key"

# Context windows for common Groq-hosted models (best effort).
_CONTEXT_HINTS = {
    "llama-3.3-70b-versatile": 131072,
    "llama-3.1-8b-instant": 131072,
    "llama-3.1-70b-versatile": 131072,
    "mixtral-8x7b-32768": 32768,
    "gemma2-9b-it": 8192,
    "qwen-2.5-coder-32b": 131072,
}


def get_api_key() -> str | None:
    """Resolve the Groq key from keyring, then environment. Never from config."""
    try:
        import keyring

        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
        if key:
            return key
    except Exception:  # pragma: no cover - keyring backend may be unavailable
        pass
    return os.environ.get("GROQ_API_KEY")


def set_api_key(key: str) -> None:
    """Store the Groq key in the OS keyring (never echoed, never committed)."""
    import keyring

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)


class GroqProvider(Provider):
    name = "groq"
    requires_api_key = True
    is_local = False

    def build_spec(self, model: str) -> ProviderSpec:
        ctx = _CONTEXT_HINTS.get(model, 32768)
        return ProviderSpec(
            name=self.name,
            model=model,
            requires_api_key=True,
            is_local=False,
            context_window=ctx,
            supports_tool_calling=True,
        )

    def resolve(self, spec: ProviderSpec):
        try:
            from langchain_groq import ChatGroq
        except ImportError as exc:  # pragma: no cover - optional extra
            raise ImportError(
                "Groq support needs the 'groq' extra: pipx install 'auto-dev-cli[groq]'"
            ) from exc

        key = get_api_key()
        if not key:
            raise RuntimeError("No Groq API key found. Run 'autodev init' or set GROQ_API_KEY.")
        return ChatGroq(model=spec.model, api_key=key, temperature=0)

    def health(self) -> tuple[bool, str]:
        key = get_api_key()
        if not key:
            return False, "No Groq API key configured (keyring/GROQ_API_KEY empty)."
        try:
            import httpx

            resp = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=5.0,
            )
            if resp.status_code == 200:
                return True, "Groq API key valid."
            return False, f"Groq API rejected the key (HTTP {resp.status_code})."
        except Exception as exc:  # pragma: no cover - network dependent
            return False, f"Could not validate Groq key: {exc}"

    def list_models(self) -> list[str]:
        key = get_api_key()
        if not key:
            return []
        try:
            import httpx

            resp = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=5.0,
            )
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data", [])]
        except Exception:  # pragma: no cover - network dependent
            return []


register_provider(GroqProvider())
