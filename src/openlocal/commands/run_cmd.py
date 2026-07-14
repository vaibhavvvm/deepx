"""``openlocal start`` / ``run`` / ``resume`` -- build the stack and drive it."""

from __future__ import annotations

from openlocal.config import Config
from openlocal.providers.base import ProviderSpec, build_spec
from openlocal.runner import run_turn
from openlocal.session import (
    SessionMeta,
    ensure_gitignore,
    load_meta,
    new_session_id,
    open_checkpointer,
    save_meta,
    thread_config,
)
from openlocal.ui import console as ui

_VALID_MODES = ("local", "smart", "web")


def _build_sandbox(config: Config, session: SessionMeta, *, yolo: bool, interactive: bool):
    from openlocal.sandbox.docker_backend import DockerSandboxBackend, SandboxLimits
    from openlocal.sandbox.image_select import select_image
    from openlocal.sandbox.policy import Policy
    from openlocal.ui.approval import ApprovalController, auto_approve

    sel   = select_image(config.project_root, config.sandbox.get("image") or None)
    image = config.sandbox.get("image") or sel.fallback_image

    limits = SandboxLimits(
        cpu_limit=str(config.sandbox.get("cpu_limit", "2")),
        memory_limit=str(config.sandbox.get("memory_limit", "4g")),
        pids_limit=int(config.sandbox.get("pids_limit", 512)),
        timeout_seconds=int(config.sandbox.get("timeout_seconds", 120)),
    )
    approval       = ApprovalController(yolo=yolo) if interactive else auto_approve
    keep_alive     = bool(config.sandbox.get("keep_alive", False))
    container_name = f"openlocal-{session.id}" if keep_alive else None

    session.image          = image
    session.network        = config.sandbox.get("network", "none")
    session.container_name = container_name or ""

    backend = DockerSandboxBackend(
        workdir=config.project_root,
        image=image,
        network=session.network,
        limits=limits,
        policy=Policy.from_config(config.policy),
        approval_callback=approval,
        yolo=yolo,
        container_name=container_name,
        keep_alive=keep_alive,
        session_id=session.id,
        protect_secret_files=bool(config.sandbox.get("protect_secret_files", True)),
    )
    return backend, sel


def _privacy_banner(spec: ProviderSpec, network: str, mode: str) -> None:
    if not spec.is_local:
        ui.cloud_switch_warning(spec)
    if mode == "web":
        ui.web_mode_warning()
    elif mode == "smart":
        ui.smart_mode_notice()
    ui.print_status(spec, network, mode)


def _preflight(config: Config, spec: ProviderSpec) -> bool:
    """Warn (not block) on obvious problems before we spin up a container."""
    from openlocal.providers.base import get_provider

    ok, msg = get_provider(spec.name).health()
    if not ok:
        ui.error(msg)
        ui.info("Run 'openlocal doctor' for a full check.")
        return False
    if not spec.supports_tool_calling:
        ui.warn(
            f"{spec.model_string} is not known to reliably support tool-calling. "
            "Subagent delegation is disabled; consider a tool-tuned model or Groq."
        )
    return True


def _run_loop(config, spec, session, *, yolo, initial_prompt, interactive, mode):
    ensure_gitignore(config.project_root)
    backend, sel = _build_sandbox(config, session, yolo=yolo, interactive=interactive)
    ui.info(f"image: {backend.image}  ({sel.reason})")

    with open_checkpointer(config.project_root, session.id) as checkpointer:
        from openlocal.agent.build import build_agent

        try:
            ui.info("starting sandbox container…")
            backend.start()
        except Exception as exc:
            ui.error(f"could not start sandbox: {exc}")
            session.touch("failed")
            save_meta(config.project_root, session)
            return 1

        try:
            agent = build_agent(
                spec,
                backend,
                config.data,
                project_root=config.project_root,
                checkpointer=checkpointer,
                mode=mode,
            )
            cfg = thread_config(session.id)
            _privacy_banner(spec, session.network, mode)

            if initial_prompt:
                session.prompt = session.prompt or initial_prompt
                save_meta(config.project_root, session)
                run_turn(agent, initial_prompt, cfg)

            if interactive:
                _repl(agent, cfg, config, spec, session, backend, checkpointer, mode)

            session.touch("done")
            save_meta(config.project_root, session)
            return 0
        except KeyboardInterrupt:
            ui.info(
                f"\ninterrupted — session checkpointed; resume with "
                f"'openlocal resume {session.id}'"
            )
            session.touch("paused")
            save_meta(config.project_root, session)
            return 130
        except Exception as exc:
            ui.error(f"agent error: {exc}")
            session.touch("failed")
            save_meta(config.project_root, session)
            return 1
        finally:
            backend.stop()


def _repl(agent, cfg, config, spec, session, backend, checkpointer, mode):
    from openlocal.commands.repl import Repl
    Repl(agent, config, spec, session, backend, checkpointer, mode=mode).run()


def _resolve_mode(config: Config, mode_override: str | None) -> str:
    """Return the effective mode: CLI flag > config > default 'local'."""
    if mode_override:
        if mode_override not in _VALID_MODES:
            raise ValueError(f"--mode must be one of: {', '.join(_VALID_MODES)}")
        return mode_override
    return config.get("tools.mode", "local")


def run_interactive(config, spec, *, initial_prompt=None, yolo=False, mode="local"):
    session = SessionMeta(id=new_session_id(), model_string=spec.model_string)
    save_meta(config.project_root, session)
    if not _preflight(config, spec):
        return 1
    return _run_loop(
        config, spec, session,
        yolo=yolo, initial_prompt=initial_prompt, interactive=True, mode=mode,
    )


def run_oneshot(config, spec, *, task, yes=False, mode="local"):
    session = SessionMeta(id=new_session_id(), model_string=spec.model_string, prompt=task)
    save_meta(config.project_root, session)
    if not _preflight(config, spec):
        return 1
    return _run_loop(
        config, spec, session,
        yolo=yes, initial_prompt=task, interactive=False, mode=mode,
    )


def run_resume(config, session_id, *, yolo=False, mode="local"):
    meta = load_meta(config.project_root, session_id)
    if meta is None:
        ui.error(f"No session '{session_id}' in {config.project_root}")
        return 1
    ui.info(f"Resuming session {session_id} (was: {meta.status})")
    spec = build_spec(meta.model_string)
    meta.touch("running")
    save_meta(config.project_root, meta)
    return _run_loop(
        config, spec, meta,
        yolo=yolo, initial_prompt=None, interactive=True, mode=mode,
    )
