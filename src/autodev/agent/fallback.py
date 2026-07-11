"""Opt-in fallback chain: try local first, fall back to Groq on trouble.

Implemented as a ``wrap_model_call`` middleware rather than a custom
``BaseChatModel`` so it composes cleanly with tool-binding and the rest of the
deepagents stack. The switch is always logged -- never silent -- because it has
cost and privacy implications (blueprint 4.4).

Triggers, gated by config ``[model] fallback_on``:
* ``timeout`` -- the primary exceeded ``fallback_timeout_seconds``.
* ``tool_call_parse_error`` / any error whose text matches a configured token.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from langchain.agents.middleware import AgentMiddleware

from autodev.providers.base import ProviderSpec, build_spec, resolve_model
from autodev.ui import console as ui


class FallbackMiddleware(AgentMiddleware):
    """Retry a failed/slow primary model call once against a fallback model."""

    name = "autodev_fallback"

    def __init__(
        self,
        fallback_spec: ProviderSpec,
        *,
        timeout_seconds: int = 45,
        fallback_on: list[str] | None = None,
    ):
        super().__init__()
        self.fallback_spec = fallback_spec
        self.timeout_seconds = timeout_seconds
        self.fallback_on = [t.lower() for t in (fallback_on or [])]
        self._fallback_model = None

    @property
    def fallback_model(self):
        if self._fallback_model is None:
            self._fallback_model = resolve_model(self.fallback_spec)
        return self._fallback_model

    def _wants_timeout(self) -> bool:
        return "timeout" in self.fallback_on

    def _matches_error(self, exc: Exception) -> bool:
        if not self.fallback_on:
            return True  # any error triggers fallback if list is empty
        text = f"{type(exc).__name__} {exc}".lower()
        tokens = [t for t in self.fallback_on if t != "timeout"]
        return any(tok.replace("_", " ") in text or tok in text for tok in tokens)

    def _switch(self, request, reason: str):
        ui.warn(
            f"primary model failed ({reason}); falling back to {self.fallback_spec.model_string}"
        )
        if not self.fallback_spec.is_local:
            ui.cloud_switch_warning(self.fallback_spec)
        return request.override(model=self.fallback_model)

    def wrap_model_call(self, request, handler: Callable):  # type: ignore[override]
        # Timeout path: run the primary in a worker thread with a wall clock.
        if self._wants_timeout():
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(handler, request)
                try:
                    return future.result(timeout=self.timeout_seconds)
                except FutureTimeout:
                    return handler(self._switch(request, f"timeout >{self.timeout_seconds}s"))
                except Exception as exc:  # noqa: BLE001
                    if self._matches_error(exc):
                        return handler(self._switch(request, type(exc).__name__))
                    raise
        # No timeout guard: only catch matching errors.
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            if self._matches_error(exc):
                return handler(self._switch(request, type(exc).__name__))
            raise


def build_fallback_middleware(config: dict) -> FallbackMiddleware | None:
    """Construct the fallback middleware from ``[model]`` config, if enabled."""
    model_cfg = config.get("model", {})
    fallback_string = (model_cfg.get("fallback") or "").strip()
    if not fallback_string:
        return None
    try:
        spec = build_spec(fallback_string)
    except Exception:
        return None
    return FallbackMiddleware(
        spec,
        timeout_seconds=int(model_cfg.get("fallback_timeout_seconds", 45)),
        fallback_on=model_cfg.get("fallback_on", ["timeout", "tool_call_parse_error"]),
    )
