"""Typer CLI: command definitions and wiring only.

Commands:

    openlocal init          interactive first-run: detect providers, write config
    openlocal doctor        health checks + tool-calling capability probe
    openlocal models ...    unified model view / pull
    openlocal start [P]     interactive REPL against a sandboxed agent
    openlocal run "<task>"  non-interactive one-shot (CI/scripts)
    openlocal resume <id>   reattach to a checkpointed session
    openlocal sessions ...  list past sessions
    openlocal sandbox shell raw shell inside the running container
    openlocal config ...    read/write layered config

Modes (--mode):
    local   Filesystem/shell tools only.  Fully private. (default)
    smart   + semantic_search via local Ollama embeddings.  Still fully private.
    web     + web_search via DuckDuckGo.  Search queries leave the machine.
    compose Multi-agent pipeline (Planner -> Coder -> Tester).
"""

from __future__ import annotations

from pathlib import Path

import typer

from openlocal import __version__
from openlocal.config import Config, load_config, set_key
from openlocal.providers.base import build_spec, get_provider, list_providers
from openlocal.ui import console as ui

app = typer.Typer(
    name="openlocal",
    help="Local-first, provider-agnostic, sandboxed coding agent.",
    no_args_is_help=True,
    add_completion=False,
)

models_app = typer.Typer(help="Inspect and pull models across providers.", no_args_is_help=True)
config_app = typer.Typer(help="Read and write layered configuration.", no_args_is_help=True)
sessions_app = typer.Typer(help="Inspect past sessions.", no_args_is_help=True)
sandbox_app = typer.Typer(help="Sandbox escape hatches.", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(config_app, name="config")
app.add_typer(sessions_app, name="sessions")
app.add_typer(sandbox_app, name="sandbox")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _resolve_spec(config: Config, model_override: str | None):
    model_string = model_override or config.model_string
    return build_spec(model_string)


# --------------------------------------------------------------------------- #
# top-level commands
# --------------------------------------------------------------------------- #
@app.callback()
def _main(ctx: typer.Context):
    """Record opt-in local telemetry for the invoked command (off by default)."""
    from openlocal import telemetry

    if ctx.invoked_subcommand:
        try:
            enabled = bool(load_config().get("telemetry.enabled", False))
        except Exception:
            enabled = False
        telemetry.record_command(ctx.invoked_subcommand, enabled=enabled)


@app.command()
def version():
    """Print the version."""
    ui.banner(__version__)


@app.command()
def init(
    project: Path = typer.Option(Path.cwd(), help="Project directory to initialise."),
):
    """Interactive first-run: detect providers and write config."""
    from openlocal.commands.init_cmd import run_init

    run_init(project)


@app.command()
def doctor(
    model: str | None = typer.Option(None, "--model", "-m", help="provider:model to test."),
    no_probe: bool = typer.Option(False, "--no-probe", help="Skip the tool-calling probe."),
):
    """Health check: docker, provider reachability, tool-calling capability."""
    from openlocal import doctor as diag

    config = load_config()
    spec = _resolve_spec(config, model)
    rows = diag.run_all(spec, probe=not no_probe)
    ui.doctor_table(rows)
    if not all(ok for _, ok, _ in rows):
        raise typer.Exit(code=1)


@app.command()
def start(
    prompt: str | None = typer.Argument(None, help="Optional initial task."),
    model: str | None = typer.Option(None, "--model", "-m"),
    network: str | None = typer.Option(None, "--network", help="none|restricted|full"),
    no_network: bool = typer.Option(False, "--no-network", help="Force network=none."),
    yolo: bool = typer.Option(False, "--yolo", help="Skip approval prompts (dangerous)."),
    mode: str = typer.Option(
        "local",
        "--mode",
        help=(
            "Tool mode: local (default, filesystem only) | "
            "smart (+ local semantic search) | "
            "web (+ DuckDuckGo web search, queries leave machine) | "
            "compose (Planner -> Coder pipeline)"
        ),
    ),
):
    """Launch the sandboxed agent REPL.

    Modes:\n
      local   — filesystem/shell tools only. Fully private. (default)\n
      smart   — + semantic_search via local Ollama embeddings. Still private.\n
      web     — + web_search via DuckDuckGo. Search queries leave the machine.\n
      compose — Multi-agent Planner -> Coder -> Tester pipeline.
    """
    from openlocal.commands.run_cmd import run_interactive

    overrides = _network_overrides(network, no_network)
    config = load_config(overrides=overrides)
    spec = _resolve_spec(config, model)
    run_interactive(config, spec, initial_prompt=prompt, yolo=yolo, mode=mode)


@app.command()
def run(
    task: str = typer.Argument(..., help="The task to perform."),
    model: str | None = typer.Option(None, "--model", "-m"),
    network: str | None = typer.Option(None, "--network"),
    no_network: bool = typer.Option(False, "--no-network"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve approval-gated commands."),
    mode: str = typer.Option(
        "local",
        "--mode",
        help="local | smart | web  (see 'openlocal start --help' for details)",
    ),
):
    """Non-interactive one-shot execution (for CI/scripts)."""
    from openlocal.commands.run_cmd import run_oneshot

    overrides = _network_overrides(network, no_network)
    config = load_config(overrides=overrides)
    spec = _resolve_spec(config, model)
    code = run_oneshot(config, spec, task=task, yes=yes, mode=mode)
    raise typer.Exit(code=code)


@app.command()
def resume(
    session_id: str = typer.Argument(..., help="Session id to reattach to."),
    yolo: bool = typer.Option(False, "--yolo"),
    mode: str = typer.Option("local", "--mode", help="local | smart | web"),
):
    """Reattach to a previous checkpointed session."""
    from openlocal.commands.run_cmd import run_resume

    config = load_config()
    run_resume(config, session_id, yolo=yolo, mode=mode)


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
@models_app.command("list")
def models_list():
    """Unified view of models across ollama / llama.cpp / groq."""
    from rich.table import Table

    table = Table(title="available models", header_style="bold")
    table.add_column("provider")
    table.add_column("local?")
    table.add_column("models")
    for provider in list_providers():
        names = provider.list_models()
        table.add_row(
            provider.name,
            "yes" if provider.is_local else "no",
            ", ".join(names) if names else "[dim](none / unreachable)[/dim]",
        )
    ui.console.print(table)


@models_app.command("pull")
def models_pull(name: str = typer.Argument(..., help="Model to pull (Ollama).")):
    """Pull a model (proxies to Ollama)."""
    provider = get_provider("ollama")
    ui.info(f"Pulling {name} via Ollama…")
    provider.pull(name)  # type: ignore[attr-defined]
    ui.success(f"Pulled {name}")


# --------------------------------------------------------------------------- #
# sessions
# --------------------------------------------------------------------------- #
@sessions_app.command("list")
def sessions_list():
    """Show past sessions with status."""
    from rich.table import Table

    from openlocal.session import list_sessions

    config = load_config()
    metas = list_sessions(config.project_root)
    if not metas:
        ui.info("No sessions yet.")
        return
    table = Table(title="sessions", header_style="bold")
    for col in ("id", "status", "model", "created", "prompt"):
        table.add_column(col)
    for m in metas:
        table.add_row(m.id, m.status, m.model_string, m.created_at[:19], (m.prompt or "")[:40])
    ui.console.print(table)


# --------------------------------------------------------------------------- #
# sandbox
# --------------------------------------------------------------------------- #
@sandbox_app.command("shell")
def sandbox_shell(
    session_id: str | None = typer.Argument(None, help="Keep-alive session id."),
):
    """Drop into a raw shell inside a running keep-alive container."""
    import subprocess

    name = f"openlocal-{session_id}" if session_id else None
    if not name:
        ui.error("Provide a keep-alive session id (see 'openlocal sessions list').")
        raise typer.Exit(1)
    ui.info(f"Attaching to {name} (Ctrl-D to exit)…")
    subprocess.call(["docker", "exec", "-it", "-w", "/workspace", name, "/bin/sh"])


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
@config_app.command("get")
def config_get(key: str = typer.Argument(..., help="Dotted key, e.g. model.default")):
    config = load_config()
    value = config.get(key)
    if value is None:
        ui.warn(f"'{key}' is not set.")
        raise typer.Exit(1)
    ui.console.print(value)


@config_app.command("set")
def config_set(
    key: str = typer.Argument(...),
    value: str = typer.Argument(...),
    global_scope: bool = typer.Option(False, "--global", help="Write to global config."),
):
    config = load_config()
    scope = "global" if global_scope else "project"
    path = set_key(key, _coerce(value), scope=scope, project_root=config.project_root)
    ui.success(f"Set {key} = {value} in {path}")


@config_app.command("telemetry")
def config_telemetry():
    """Show the locally-collected, opt-in usage counters (never uploaded)."""
    from rich.table import Table

    from openlocal import telemetry

    data = telemetry.summary()
    if not data:
        ui.info("No telemetry collected (disabled by default).")
        return
    table = Table(title="local telemetry", header_style="bold")
    table.add_column("command")
    table.add_column("count")
    table.add_column("last used")
    for name, entry in sorted(data.items()):
        table.add_row(name, str(entry.get("count", 0)), entry.get("last", "")[:19])
    ui.console.print(table)


@config_app.command("edit")
def config_edit(
    global_scope: bool = typer.Option(False, "--global"),
):
    import os
    import subprocess

    from openlocal.config import GLOBAL_CONFIG_PATH

    config = load_config()
    path = GLOBAL_CONFIG_PATH if global_scope else config.project_root / ".openlocal.toml"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    editor = os.environ.get("EDITOR", "nano")
    subprocess.call([editor, str(path)])


# --------------------------------------------------------------------------- #
# internal
# --------------------------------------------------------------------------- #
def _network_overrides(network: str | None, no_network: bool) -> dict | None:
    if no_network:
        return {"sandbox": {"network": "none"}}
    if network:
        if network not in ("none", "restricted", "full"):
            raise typer.BadParameter("network must be none|restricted|full")
        return {"sandbox": {"network": network}}
    return None


def _coerce(value: str):
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if value.isdigit():
        return int(value)
    return value


if __name__ == "__main__":
    app()
