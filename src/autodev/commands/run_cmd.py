"""``autodev start`` / ``run`` / ``resume`` -- build the stack and drive it."""

from __future__ import annotations

from autodev.config import Config
from autodev.providers.base import ProviderSpec, build_spec
from autodev.runner import run_turn
from autodev.session import (
    SessionMeta,
    ensure_gitignore,
    load_meta,
    new_session_id,
    open_checkpointer,
    save_meta,
    thread_config,
)
from autodev.ui import console as ui


def _build_sandbox(config: Config, session: SessionMeta, *, yolo: bool, interactive: bool):
    from autodev.sandbox.docker_backend import DockerSandboxBackend, SandboxLimits
    from autodev.sandbox.image_select import select_image
    from autodev.sandbox.policy import Policy
    from autodev.ui.approval import ApprovalController, auto_approve

    sel = select_image(config.project_root, config.sandbox.get("image") or None)
    # Use the Docker Hub fallback image unless the user pinned one, so a fresh
    # machine works before the project's own images are published.
    image = config.sandbox.get("image") or sel.fallback_image

    limits = SandboxLimits(
        cpu_limit=str(config.sandbox.get("cpu_limit", "2")),
        memory_limit=str(config.sandbox.get("memory_limit", "4g")),
        pids_limit=int(config.sandbox.get("pids_limit", 512)),
        timeout_seconds=int(config.sandbox.get("timeout_seconds", 120)),
    )
    approval = ApprovalController(yolo=yolo) if interactive else auto_approve
    keep_alive = bool(config.sandbox.get("keep_alive", False))
    container_name = f"autodev-{session.id}" if keep_alive else None

    session.image = image
    session.network = config.sandbox.get("network", "none")
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


def _privacy_banner(spec: ProviderSpec, network: str) -> None:
    if not spec.is_local:
        ui.cloud_switch_warning(spec)
    ui.print_status(spec, network)


def _preflight(config: Config, spec: ProviderSpec) -> bool:
    """Warn (not block) on obvious problems before we spin up a container."""
    from autodev.providers.base import get_provider

    ok, msg = get_provider(spec.name).health()
    if not ok:
        ui.error(msg)
        ui.info("Run 'autodev doctor' for a full check.")
        return False
    if not spec.supports_tool_calling:
        ui.warn(
            f"{spec.model_string} is not known to reliably support tool-calling. "
            "Subagent delegation is disabled; consider a tool-tuned model or Groq."
        )
    return True


def _run_loop(config, spec, session, *, yolo, initial_prompt, interactive):
    ensure_gitignore(config.project_root)
    backend, sel = _build_sandbox(config, session, yolo=yolo, interactive=interactive)
    ui.info(f"image: {backend.image}  ({sel.reason})")

    with open_checkpointer(config.project_root, session.id) as checkpointer:
        from autodev.agent.build import build_agent

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
            )
            cfg = thread_config(session.id)
            _privacy_banner(spec, session.network)

            if initial_prompt:
                session.prompt = session.prompt or initial_prompt
                save_meta(config.project_root, session)
                run_turn(agent, initial_prompt, cfg)

            if interactive:
                _repl(agent, cfg, config, spec, session, backend, checkpointer)

            session.touch("done")
            save_meta(config.project_root, session)
            return 0
        except KeyboardInterrupt:
            ui.info(
                f"\ninterrupted — session checkpointed; resume with 'autodev resume {session.id}'"
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


def _repl(agent, cfg, config, spec, session, backend, checkpointer):
    from autodev.commands.repl import Repl

    Repl(agent, config, spec, session, backend, checkpointer).run()


def run_interactive(config, spec, *, initial_prompt=None, yolo=False):
    session = SessionMeta(id=new_session_id(), model_string=spec.model_string)
    save_meta(config.project_root, session)
    if not _preflight(config, spec):
        return 1
    return _run_loop(
        config, spec, session, yolo=yolo, initial_prompt=initial_prompt, interactive=True
    )


def run_oneshot(config, spec, *, task, yes=False):
    session = SessionMeta(id=new_session_id(), model_string=spec.model_string, prompt=task)
    save_meta(config.project_root, session)
    if not _preflight(config, spec):
        return 1
    return _run_loop(config, spec, session, yolo=yes, initial_prompt=task, interactive=False)


def run_resume(config, session_id, *, yolo=False):
    meta = load_meta(config.project_root, session_id)
    if meta is None:
        ui.error(f"No session '{session_id}' in {config.project_root}")
        return 1
    ui.info(f"Resuming session {session_id} (was: {meta.status})")
    spec = build_spec(meta.model_string)
    meta.touch("running")
    save_meta(config.project_root, meta)
    return _run_loop(config, spec, meta, yolo=yolo, initial_prompt=None, interactive=True)
