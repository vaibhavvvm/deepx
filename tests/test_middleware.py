"""Fallback and tool-call-retry middleware behaviour."""

from dataclasses import dataclass, field, replace
from types import SimpleNamespace

from autodev.agent.fallback import FallbackMiddleware
from autodev.agent.retry import ToolCallRetryMiddleware
from autodev.providers.base import build_spec


@dataclass
class FakeRequest:
    model: object = "primary"
    messages: list = field(default_factory=list)

    def override(self, **kw):
        return replace(self, **kw)


def test_fallback_on_matching_error():
    spec = build_spec("ollama:llama3.1")  # local => resolve builds ChatOllama, no net
    mw = FallbackMiddleware(spec, fallback_on=["tool_call_parse_error"])
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("tool_call_parse_error: bad json")
        return SimpleNamespace(model=req.model, result="OK")

    out = mw.wrap_model_call(FakeRequest(), handler)
    assert out.result == "OK"
    assert calls["n"] == 2
    assert out.model is mw.fallback_model  # retried against fallback


def test_fallback_ignores_unmatched_error():
    spec = build_spec("ollama:llama3.1")
    mw = FallbackMiddleware(spec, fallback_on=["tool_call_parse_error"])

    def handler(req):
        raise RuntimeError("some unrelated boom")

    try:
        mw.wrap_model_call(FakeRequest(), handler)
        raised = False
    except RuntimeError:
        raised = True
    assert raised  # unmatched error propagates, no fallback


def test_fallback_timeout(monkeypatch):
    import time

    spec = build_spec("ollama:llama3.1")
    mw = FallbackMiddleware(spec, fallback_on=["timeout"], timeout_seconds=1)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(2)  # exceed the 1s budget
            return SimpleNamespace(result="SLOW")
        return SimpleNamespace(result="FALLBACK", model=req.model)

    out = mw.wrap_model_call(FakeRequest(), handler)
    assert out.result == "FALLBACK"


def test_retry_on_invalid_tool_call():
    mw = ToolCallRetryMiddleware(max_retries=1)
    bad_ai = SimpleNamespace(content="", invalid_tool_calls=[{"name": "add", "error": "bad json"}])
    good_ai = SimpleNamespace(content="done", invalid_tool_calls=[])
    responses = [
        SimpleNamespace(result=[bad_ai]),
        SimpleNamespace(result=[good_ai]),
    ]
    calls = {"n": 0}

    def handler(req):
        r = responses[calls["n"]]
        calls["n"] += 1
        return r

    out = mw.wrap_model_call(FakeRequest(), handler)
    assert out.result[0] is good_ai
    assert calls["n"] == 2


def test_retry_noop_when_valid():
    mw = ToolCallRetryMiddleware(max_retries=1)
    good = SimpleNamespace(content="ok", invalid_tool_calls=[])
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return SimpleNamespace(result=[good])

    mw.wrap_model_call(FakeRequest(), handler)
    assert calls["n"] == 1  # no retry
