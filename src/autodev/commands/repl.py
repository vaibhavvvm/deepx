"""Interactive REPL with slash commands (opencode-style).

Owns the conversation loop for ``autodev start`` / ``resume``. Slash commands
let the user drive the session without leaving it: switch models, inspect the
diff, run a raw sandbox command, clear/compact context, list sessions.
"""

from __future__ import annotations

from rich.prompt import Prompt
from rich.table import Table

from autodev.providers.base import build_spec, list_providers
from autodev.runner import run_turn
from autodev.session import (
    SessionMeta,
    list_sessions,
    new_session_id,
    save_meta,
    thread_config,
)
from autodev.ui import console as ui

_HELP = [
    ("/help", "show this help"),
    ("/model <provider:model>", "switch model mid-session (cloud switch warns)"),
    ("/models", "list available models across providers"),
    ("/status", "show active model and network posture"),
    ("/diff", "show git diff of changes made in the sandbox"),
    ("/shell <cmd>", "run a raw command in the sandbox (policy still applies)"),
    ("/clear", "start a fresh context (new thread, same container)"),
    ("/compact", "summarize the session so far and continue with less context"),
    ("/sessions", "list past sessions in this project"),
    ("/init", "write a starter AGENTS.md with project house rules"),
    ("/exit", "quit (also /quit, :q, Ctrl-D)"),
]


class Repl:
    def __init__(self, agent, config, spec, session: SessionMeta, backend, checkpointer):
        self.agent = agent
        self.config = config
        self.spec = spec
        self.session = session
        self.backend = backend
        self.checkpointer = checkpointer
        self.cfg = thread_config(session.id)
        self.carryover = ""  # summary injected after /compact

    # -- main loop -------------------------------------------------------------
    def run(self) -> None:
        ui.info("Interactive session. /help for commands, /exit to quit.")
        while True:
            try:
                line = Prompt.ask("\n[bold cyan]autodev[/bold cyan]")
            except (EOFError, KeyboardInterrupt):
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/exit", "/quit", ":q"):
                break
            if line.startswith("/"):
                self._dispatch(line)
                continue
            self._send(line)

    def _send(self, text: str) -> None:
        if self.carryover:
            text = (
                "Context from earlier in this session (summary):\n"
                f"{self.carryover}\n\n---\n\nNew request: {text}"
            )
            self.carryover = ""
        run_turn(self.agent, text, self.cfg)

    # -- slash command dispatch ------------------------------------------------
    def _dispatch(self, line: str) -> None:
        parts = line.split(maxsplit=1)
        cmd, arg = parts[0], (parts[1] if len(parts) > 1 else "")
        handler = {
            "/help": self._help,
            "/model": lambda: self._model(arg),
            "/models": self._models,
            "/status": self._status,
            "/diff": self._diff,
            "/shell": lambda: self._shell(arg),
            "/clear": self._clear,
            "/new": self._clear,
            "/compact": self._compact,
            "/sessions": self._sessions,
            "/init": self._init,
        }.get(cmd)
        if handler is None:
            ui.warn(f"unknown command {cmd} — try /help")
            return
        handler()

    def _help(self) -> None:
        table = Table(title="commands", header_style="bold", show_header=True)
        table.add_column("command")
        table.add_column("what")
        for name, desc in _HELP:
            table.add_row(name, desc)
        ui.console.print(table)

    def _model(self, arg: str) -> None:
        if not arg:
            ui.warn("usage: /model <provider:model>")
            return
        try:
            new_spec = build_spec(arg.strip())
        except Exception as exc:
            ui.error(str(exc))
            return
        # Critical UX rule: into cloud warns; back to local is quiet.
        if new_spec.is_local:
            ui.local_switch_notice(new_spec)
        else:
            ui.cloud_switch_warning(new_spec)
        self.spec = new_spec
        self.session.model_string = new_spec.model_string
        save_meta(self.config.project_root, self.session)
        self._rebuild_agent()
        ui.print_status(self.spec, self.session.network)

    def _rebuild_agent(self) -> None:
        from autodev.agent.build import build_agent

        self.agent = build_agent(
            self.spec,
            self.backend,
            self.config.data,
            project_root=self.config.project_root,
            checkpointer=self.checkpointer,
        )

    def _models(self) -> None:
        table = Table(title="available models", header_style="bold")
        table.add_column("provider")
        table.add_column("models")
        for provider in list_providers():
            names = provider.list_models()
            table.add_row(provider.name, ", ".join(names) if names else "[dim]—[/dim]")
        ui.console.print(table)

    def _status(self) -> None:
        ui.print_status(self.spec, self.session.network)

    def _diff(self) -> None:
        res = self.backend.execute(
            "git -C /workspace diff --stat && echo '---' && git -C /workspace diff"
        )
        ui.console.print(res.output or "[dim]no changes[/dim]")

    def _shell(self, arg: str) -> None:
        if not arg:
            ui.warn("usage: /shell <command>")
            return
        res = self.backend.execute(arg)
        ui.console.print(res.output)
        ui.info(f"exit {res.exit_code}")

    def _clear(self) -> None:
        # New thread id => fresh context; the container and files are untouched.
        self.cfg = thread_config(new_session_id())
        ui.success("context cleared (files preserved). New conversation thread.")

    def _compact(self) -> None:
        ui.info("summarizing session…")
        summary = run_turn(
            self.agent,
            "Summarize concisely what we've done so far, decisions made, and the "
            "current state of the work. This summary will seed a fresh context.",
            self.cfg,
        )
        self.carryover = summary or ""
        new_id = new_session_id()
        self.cfg = thread_config(new_id)
        ui.success("compacted: continuing with a summarized context.")

    def _sessions(self) -> None:
        metas = list_sessions(self.config.project_root)
        table = Table(title="sessions", header_style="bold")
        for col in ("id", "status", "model", "created"):
            table.add_column(col)
        for m in metas[:20]:
            table.add_row(m.id, m.status, m.model_string, m.created_at[:19])
        ui.console.print(table)

    def _init(self) -> None:
        path = self.config.project_root / "AGENTS.md"
        if path.exists():
            ui.warn("AGENTS.md already exists.")
            return
        path.write_text(_AGENTS_TEMPLATE, encoding="utf-8")
        ui.success(f"wrote {path} — edit it to teach the agent house rules.")


_AGENTS_TEMPLATE = """\
# Project conventions for the Auto-Dev agent

Document house rules the agent must follow in this repo. Examples:

- Always run the formatter before finishing (e.g. `ruff format` / `prettier`).
- Tests live in `tests/`; run them with `<your test command>`.
- Never touch `migrations/` without asking.
- Prefer editing existing modules over adding new files.
"""
