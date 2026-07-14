"""Health checks and the tool-calling capability probe (``openlocal doctor``)."""

from __future__ import annotations

from openlocal.providers.base import ProviderSpec, get_provider, resolve_model

Row = tuple[str, bool, str]  # (check name, ok, detail)


def check_docker() -> Row:
    try:
        import docker

        client = docker.from_env()
        client.ping()
        version = client.version().get("Version", "?")
        return ("docker daemon", True, f"reachable (engine {version})")
    except Exception as exc:
        return ("docker daemon", False, f"not reachable: {exc}")


def check_docker_roundtrip(image: str = "python:3.12-slim") -> Row:
    """Write-then-read round trip inside a throwaway container.

    Catches Docker Desktop / WSL2 path-translation and permission issues early
    (blueprint 7.5) instead of surfacing them as raw SDK tracebacks mid-task.
    """
    try:
        import docker

        client = docker.from_env()
        try:
            client.images.get(image)
        except docker.errors.ImageNotFound:
            client.images.pull(image)
        out = client.containers.run(
            image,
            command=["/bin/sh", "-c", "echo openlocal-ok > /tmp/probe && cat /tmp/probe"],
            remove=True,
        )
        text = out.decode() if isinstance(out, bytes) else str(out)
        ok = "openlocal-ok" in text
        return ("docker write/read", ok, "round trip ok" if ok else "unexpected output")
    except Exception as exc:
        return ("docker write/read", False, f"failed: {exc}")


def check_provider(spec: ProviderSpec) -> Row:
    provider = get_provider(spec.name)
    ok, msg = provider.health()
    return (f"provider: {spec.name}", ok, msg)


def probe_tool_calling(spec: ProviderSpec) -> Row:
    """Scripted tool-call test; records ``supports_tool_calling`` empirically.

    We bind a trivial tool and ask the model to call it. If the model returns a
    well-formed tool call, tool-calling works. Failure is a warning, not fatal.
    """
    try:
        from langchain_core.tools import tool

        @tool
        def add(a: int, b: int) -> int:
            """Add two integers."""
            return a + b

        model = resolve_model(spec)
        bound = model.bind_tools([add])
        resp = bound.invoke("Use the add tool to compute 2 + 3. Call the tool.")
        calls = getattr(resp, "tool_calls", None) or []
        if calls and calls[0].get("name") == "add":
            return ("tool-calling probe", True, f"{spec.model_string} emitted a valid tool call")
        return (
            "tool-calling probe",
            False,
            f"{spec.model_string} did not emit a tool call; consider a "
            "tool-tuned model (Qwen2.5-Coder, Llama-3.1-instruct) or Groq",
        )
    except Exception as exc:
        return ("tool-calling probe", False, f"probe error: {exc}")


def run_all(
    spec: ProviderSpec, *, image: str = "python:3.12-slim", probe: bool = True
) -> list[Row]:
    rows: list[Row] = [check_docker()]
    if rows[0][1]:  # only round-trip if the daemon is up
        rows.append(check_docker_roundtrip(image))
    rows.append(check_provider(spec))
    if probe:
        rows.append(probe_tool_calling(spec))
    return rows
