"""Provider registry and model resolution.

The model layer is a pluggable provider system, not a hard-coded call. A
``Provider`` knows how to describe itself (:class:`ProviderSpec`), health-check
itself, and produce a LangChain chat model. Providers register themselves in a
module-level registry so third parties can extend the set via entry points.

Resolution flow (see :func:`resolve_model`):

    provider string  ->  ProviderSpec  ->  BaseChatModel
    "ollama:qwen2.5-coder:7b"           ChatOllama(...)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel


@dataclass
class ProviderSpec:
    """A fully-resolved description of one model on one provider.

    This is the currency the rest of the system trades in: the sandbox network
    policy, context budgeting, subagent enablement, and the cloud/local privacy
    banner are all decided from these fields.
    """

    name: str  # "ollama" | "llamacpp" | "groq"
    model: str  # bare model name, e.g. "qwen2.5-coder:7b"
    requires_api_key: bool
    is_local: bool
    context_window: int = 8192
    supports_tool_calling: bool = True
    # Free-form extras a provider may attach (base_url, port, ...).
    extra: dict = field(default_factory=dict)

    @property
    def model_string(self) -> str:
        """The canonical ``provider:model`` string."""
        return f"{self.name}:{self.model}"

    @property
    def label(self) -> str:
        """Human-facing short label used in the status footer."""
        return "local" if self.is_local else f"cloud: {self.name}"


class Provider:
    """Base class every provider implements.

    Subclasses set :attr:`name` and implement :meth:`build_spec`,
    :meth:`resolve` and :meth:`health`.
    """

    name: str = ""
    requires_api_key: bool = False
    is_local: bool = True

    def build_spec(self, model: str) -> ProviderSpec:  # pragma: no cover - overridden
        raise NotImplementedError

    def resolve(self, spec: ProviderSpec) -> BaseChatModel:  # pragma: no cover
        raise NotImplementedError

    def health(self) -> tuple[bool, str]:  # pragma: no cover - overridden
        """Return ``(ok, message)`` describing reachability/credentials."""
        raise NotImplementedError

    def list_models(self) -> list[str]:  # pragma: no cover - optional
        """Return installed/available model names, best-effort."""
        return []


_REGISTRY: dict[str, Provider] = {}


def register_provider(provider: Provider) -> None:
    """Register a provider instance under ``provider.name``."""
    if not provider.name:
        raise ValueError("Provider.name must be set")
    _REGISTRY[provider.name] = provider


def get_provider(name: str) -> Provider:
    """Look up a registered provider, raising a helpful error if missing."""
    _ensure_builtin_providers()
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown provider '{name}'. Known providers: {known}") from None


def list_providers() -> list[Provider]:
    """All registered providers."""
    _ensure_builtin_providers()
    return list(_REGISTRY.values())


def parse_model_string(model_string: str) -> tuple[str, str]:
    """Split a ``provider:model`` string.

    Model names may themselves contain colons (``ollama:qwen2.5-coder:7b``), so
    we only split on the first colon.

    >>> parse_model_string("ollama:qwen2.5-coder:7b")
    ('ollama', 'qwen2.5-coder:7b')
    """
    if ":" not in model_string:
        raise ValueError(
            f"Model string '{model_string}' must be 'provider:model', "
            "e.g. 'ollama:qwen2.5-coder:7b' or 'groq:llama-3.3-70b-versatile'."
        )
    name, model = model_string.split(":", 1)
    name = name.strip().lower()
    model = model.strip()
    if not name or not model:
        raise ValueError(f"Malformed model string '{model_string}'.")
    return name, model


def build_spec(model_string: str) -> ProviderSpec:
    """Turn a ``provider:model`` string into a :class:`ProviderSpec`."""
    name, model = parse_model_string(model_string)
    return get_provider(name).build_spec(model)


def resolve_model(spec: ProviderSpec) -> BaseChatModel:
    """Instantiate the LangChain chat model for a spec."""
    return get_provider(spec.name).resolve(spec)


_BUILTINS_LOADED = False


def _ensure_builtin_providers() -> None:
    """Lazily import built-in providers so registration happens once.

    Importing here (rather than at module import) avoids a circular import and
    keeps optional dependencies (``langchain-groq``) from being required until a
    Groq spec is actually resolved.
    """
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    _BUILTINS_LOADED = True
    from autodev.providers import (  # noqa: F401  (import triggers registration)
        groq_provider,
        llamacpp_provider,
        ollama_provider,
    )

    # Entry-point providers (third parties): autodev.providers group.
    _load_entrypoint_providers()


def _load_entrypoint_providers() -> None:
    """Discover third-party providers via the ``autodev.providers`` entry point."""
    try:
        from importlib.metadata import entry_points

        eps = entry_points(group="autodev.providers")
    except Exception:  # pragma: no cover - importlib edge cases
        return
    for ep in eps:
        try:
            factory: Callable[[], Provider] = ep.load()
            register_provider(factory())
        except Exception:  # pragma: no cover - a broken plugin must not crash us
            continue
