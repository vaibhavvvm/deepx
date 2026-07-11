# Auto-Dev CLI

A **local-first, provider-agnostic, sandboxed** coding-agent CLI built on
[`deepagents`](https://github.com/langchain-ai/deepagents) (LangChain's agent
harness). Point it at a repo, describe a task, and it plans, edits, and verifies
the change — inside a Docker sandbox, with a human approval gate on anything
risky.

- **Runs fully offline** against **Ollama** or **llama.cpp**. **Groq** is opt-in
  cloud, clearly flagged as *your code leaves this machine*.
- **Swap models at runtime** — `/model groq:llama-3.3-70b-versatile` mid-session.
- **Safety before capability** — deny-list is absolute, risky commands need a
  `y`, secrets are scrubbed before any cloud call.
- **Resumable** — every session is checkpointed to SQLite; `autodev resume <id>`.

> Docs: [`ARCHITECTURE.md`](./ARCHITECTURE.md) (how the code works) ·
> [`COMMANDS.md`](./COMMANDS.md) (every command) ·
> [`SECURITY.md`](./SECURITY.md) (threat model).

---

## Quick start

```bash
# 1. prerequisites: Docker running, Ollama (or llama.cpp / a Groq key), Python 3.12+
docker ps
ollama pull qwen2.5-coder:7b

# 2. install
pipx install auto-dev-cli            # Ollama support (light)
pipx install "auto-dev-cli[all]"     # + Groq + llama.cpp

# 3. set up + verify
autodev init
autodev doctor

# 4. go
autodev start "add a null check to user_service.py"
```

Developing from this repo instead? Use `uv`:

```bash
uv sync --extra all
uv run autodev doctor
uv run autodev start
```

---

## Why another coding-agent CLI?

Most agent CLIs assume a single hosted frontier model and a trusted shell. This
one assumes the opposite and designs for it:

| Concern | Approach |
|---|---|
| **Privacy** | Local models by default; nothing leaves the machine unless you pick a cloud model, and even then secrets are redacted first. |
| **Safety** | Model runs in Docker (unprivileged, resource-limited, network-off by default). Deny-list refuses destructive commands before Docker is even called. |
| **Small models** | 4B–8B local models are the common case: capability probing, structured retry on malformed tool calls, and a trimmed tool surface when tool-calling is unreliable. |
| **Mixed fleets** | Different model per subagent (cheap local tester, bigger coder), and an opt-in local→cloud fallback chain. |
| **Resumability** | LangGraph SQLite checkpoints; resume after a crash, Ctrl-C, or laptop sleep. |

---

## Providers — which to pick

| Provider | Use when | Data leaves machine? |
|---|---|---|
| **Ollama** | Easiest local setup; `ollama pull` and go | No |
| **llama.cpp** | You want raw GGUF / performance tuning | No |
| **Groq** | Local hardware too slow, or you need a bigger model | **Yes** (BYO key) |

Set a default: `autodev config set model.default ollama:qwen2.5-coder:7b`
Override per-run: `autodev start -m groq:llama-3.3-70b-versatile`

---

## Safety model in one paragraph

The **LLM call happens on your host**; only the **agent's shell/file tool calls**
run inside the container — so `network=none` is a safe default that still lets
the model think. Every command is classified by a policy: **deny** (refused
before Docker, no override), **approve** (interactive `y/n/a`, unless `--yolo`),
or **allow**. Switching to a cloud model prints a one-line warning and installs a
middleware that redacts secret-shaped strings from every request. Full details
and the threat model live in [`SECURITY.md`](./SECURITY.md).

---

## Commands at a glance

```
autodev init | doctor
autodev start [PROMPT]              # interactive REPL
autodev run "<task>" --yes         # one-shot (CI)
autodev resume <id> | sessions list
autodev models list | pull <name>
autodev config get/set/edit | telemetry
autodev sandbox shell <id>
```

In-REPL: `/help /model /models /status /diff /shell /clear /compact /sessions
/init /exit`. See [`COMMANDS.md`](./COMMANDS.md) for the full reference.

---

## Configuration

Layered, later wins: `~/.autodev/config.toml` (global) → `<repo>/.autodev.toml`
(committed, no secrets) → CLI flags. Example project config:

```toml
[model]
default = "ollama:qwen2.5-coder:7b"
# fallback = "groq:llama-3.3-70b-versatile"   # opt-in local→cloud

[sandbox]
network = "none"          # none | restricted | full
memory_limit = "4g"
timeout_seconds = 120

[policy]
require_approval_for = ["git push", "rm -rf", "npm publish", "pip install", "curl"]
deny = ["shutdown", "reboot", "mkfs", "dd if="]

[subagents.models]        # optional per-role models
coder = "groq:llama-3.3-70b-versatile"
tester = "ollama:qwen2.5-coder:7b"
```

Drop an `AGENTS.md` at the repo root to teach house rules ("run the formatter
before finishing", "never touch `migrations/`") — loaded into the agent
automatically.

---

## Extending

- **New provider**: implement `Provider` and register via the `autodev.providers`
  entry point — `pip install autodev-provider-vllm`, no core change.
- **New sandbox**: anything implementing deepagents' `SandboxBackendProtocol`
  (e.g. Podman, a cloud sandbox) can replace the Docker backend.

---

## License

MIT — see [`LICENSE`](./LICENSE).
