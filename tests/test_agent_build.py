from openlocal.agent.build import _should_enable_subagents, load_prompt
from openlocal.providers.base import build_spec
from openlocal.agent.subagents import build_subagents


def _cfg(enabled=True):
    return {"subagents": {"enable_task_delegation": enabled, "models": {}}}


def test_subagents_enabled_for_capable_model():
    spec = build_spec("ollama:qwen2.5-coder:7b")  # tool-tuned, 32k ctx
    assert _should_enable_subagents(spec, _cfg())


def test_subagents_disabled_for_small_model():
    spec = build_spec("ollama:tiny-1b")  # unknown family => no tool calling
    assert not _should_enable_subagents(spec, _cfg())


def test_subagents_disabled_by_config():
    spec = build_spec("ollama:qwen2.5-coder:7b")
    assert not _should_enable_subagents(spec, _cfg(enabled=False))


def test_prompts_load():
    for name in ("system.md", "planner.md", "coder.md", "tester.md", "reviewer.md"):
        assert load_prompt(name).strip()


def test_subagent_specs_build_with_overrides():
    from openlocal.agent.subagents import build_subagents

    subs = build_subagents({"coder": "groq:llama-3.3-70b-versatile"})
    names = {s["name"] for s in subs}
    assert {"planner", "coder", "tester", "reviewer"} == names
    coder = next(s for s in subs if s["name"] == "coder")
    assert coder["model"] == "groq:llama-3.3-70b-versatile"
