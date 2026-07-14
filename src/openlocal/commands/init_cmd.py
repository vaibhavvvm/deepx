"""``openlocal init`` -- interactive first-run wizard.

Detects what is actually available on the machine (Ollama models, a running
llama-server, an existing Groq key), lets the user pick a default model, and
writes a project ``.openlocal.toml``. Secrets go to the keyring, never the file.
"""

from __future__ import annotations

from pathlib import Path

from rich.prompt import Confirm, Prompt

from openlocal.config import write_project
from openlocal.providers.base import get_provider
from openlocal.session import ensure_gitignore
from openlocal.ui import console as ui


def run_init(project: Path) -> None:
    project = project.resolve()
    ui.banner("init")
    ui.info(f"Configuring project at {project}")

    ollama = get_provider("ollama")
    ollama_ok, ollama_msg = ollama.health()
    ui.console.print(("[green]" if ollama_ok else "[red]") + ollama_msg + "[/]")

    models = ollama.list_models() if ollama_ok else []
    default_model = "ollama:qwen2.5-coder:7b"

    if models:
        ui.info("Installed Ollama models:")
        for m in models:
            ui.console.print(f"  • {m}")
        chosen = Prompt.ask(
            "Default model (provider:model)",
            default=f"ollama:{models[0]}",
        )
        default_model = chosen
    else:
        ui.warn("No Ollama models found. You can 'openlocal models pull qwen2.5-coder:7b' later.")
        default_model = Prompt.ask("Default model (provider:model)", default=default_model)

    # llama.cpp probe (optional).
    llamacpp = get_provider("llamacpp")
    lc_ok, lc_msg = llamacpp.health()
    if lc_ok:
        ui.success(lc_msg)

    # Groq key (optional, cloud).
    if Confirm.ask("Configure a Groq API key for cloud fallback?", default=False):
        _configure_groq()

    network = Prompt.ask(
        "Default sandbox network policy",
        choices=["none", "restricted", "full"],
        default="none",
    )

    data = {
        "model": {"default": default_model},
        "sandbox": {"network": network},
    }
    path = write_project(project, data)
    ensure_gitignore(project)
    ui.success(f"Wrote {path}")
    ui.info("Run 'openlocal doctor' to verify, then 'openlocal start' to begin.")


def _configure_groq() -> None:
    from openlocal.providers.groq_provider import GroqProvider, set_api_key

    key = Prompt.ask("Groq API key (stored in OS keyring, never committed)", password=True)
    if not key:
        ui.warn("No key entered; skipping.")
        return
    try:
        set_api_key(key)
    except Exception as exc:
        ui.error(f"Could not store key in keyring: {exc}. Set GROQ_API_KEY instead.")
        return
    ok, msg = GroqProvider().health()
    ui.console.print(("[green]" if ok else "[red]") + msg + "[/]")
