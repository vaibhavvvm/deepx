# Auto-Dev CLI — Setup & Commands

Everything you need to install, configure, and run the tool. For how the code
works, see [`ARCHITECTURE.md`](./ARCHITECTURE.md).

---

## 1. Prerequisites

| Need | Why |
|---|---|
| **Docker** (running daemon) | Sandboxed command execution |
| **Ollama** *or* **llama.cpp** *or* **Groq key** | At least one model provider |
| **Python 3.12+** | Runtime |
| **uv** (dev) or **pipx** (install) | Package management |

Check they exist:

```bash
docker ps          # daemon reachable?
ollama list        # local models installed?
python3 --version  # 3.12+
```

---

## 2. Setup

### A. Development (from this repo, using uv)

```bash
# from the project root
uv sync                        # install core deps into .venv
uv sync --extra all            # + groq + llama.cpp extras
uv run autodev --help          # run without activating the venv
```

### B. Install as a global tool (pipx)

```bash
pipx install auto-dev-cli               # Ollama support only (light)
pipx install "auto-dev-cli[all]"        # + Groq + llama.cpp
pipx install "auto-dev-cli[groq]"       # + Groq only
```

After a pipx install the command is just `autodev` (drop the `uv run` prefix
from every example below).

### C. Pull a coding model (Ollama)

```bash
autodev models pull qwen2.5-coder:7b
# or use the ollama CLI directly:
ollama pull qwen2.5-coder:7b
```

### D. (Optional) Groq cloud key

```bash
# stored in the OS keyring via the init wizard, or as an env var for CI:
export GROQ_API_KEY=gsk_...
```

---

## 3. First run

```bash
uv run autodev init        # detect providers, pick default model, write .autodev.toml
uv run autodev doctor      # verify docker + provider + tool-calling capability
```

---

## 4. Core commands

```bash
# interactive agent REPL (main entry)
uv run autodev start
uv run autodev start "add a null check to user_service.py"

# pick a model for this run
uv run autodev start -m ollama:qwen2.5-coder:7b
uv run autodev start -m groq:llama-3.3-70b-versatile

# non-interactive one-shot (CI / scripts) — auto-approves gated commands
uv run autodev run "fix the failing test in tests/test_math.py" --yes
uv run autodev run "bump version to 1.2.0" -m groq:llama-3.3-70b-versatile -y

# resume a previous / crashed / paused session
uv run autodev sessions list
uv run autodev resume <session_id>
```

---

## 5. Network & safety flags

```bash
uv run autodev start --no-network           # force container network off (safest)
uv run autodev start --network restricted   # host-local Ollama/llama.cpp reachable
uv run autodev start --network full          # allow pip/npm/go installs
uv run autodev start --yolo                  # skip approval prompts (DANGEROUS)
uv run autodev run "..." --yes               # auto-approve in non-interactive mode
```

`--yolo` skips the **approval** gate only. The **deny-list** (fork bombs, `mkfs`,
`dd if=`, etc.) is always enforced.

---

## 6. In-REPL slash commands

Inside `autodev start` (type `/help` to see this in the app):

```
> /help                                  # list all commands
> /model groq:llama-3.3-70b-versatile   # switch model mid-session (cloud warns)
> /model ollama:qwen2.5-coder:7b        # switch back to local (quiet)
> /models                                # list available models across providers
> /status                                # show active model + network posture
> /diff                                  # git diff of changes made in the sandbox
> /shell <cmd>                           # run a raw command (policy still applies)
> /clear                                 # fresh context, new thread (files kept)
> /compact                               # summarize session, continue leaner
> /sessions                              # list past sessions in this project
> /init                                  # write a starter AGENTS.md
> /exit                                  # quit (also /quit, :q, Ctrl-D)
```

---

## 7. Models

```bash
uv run autodev models list              # unified view across ollama/llamacpp/groq
uv run autodev models pull <name>       # pull via Ollama
```

---

## 8. Configuration

```bash
uv run autodev config get model.default
uv run autodev config set model.default ollama:qwen2.5-coder:7b
uv run autodev config set sandbox.network none
uv run autodev config set model.default groq:llama-3.3-70b-versatile --global
uv run autodev config edit               # open .autodev.toml in $EDITOR
uv run autodev config edit --global      # open ~/.autodev/config.toml
uv run autodev config telemetry          # show local, opt-in usage counters
```

Example `.autodev.toml` (committed, no secrets):

```toml
[model]
default = "ollama:qwen2.5-coder:7b"
# opt-in local→cloud fallback on slow/failed local runs:
# fallback = "groq:llama-3.3-70b-versatile"
# fallback_on = ["timeout", "tool_call_parse_error"]
# fallback_timeout_seconds = 45

[sandbox]
network = "none"              # none | restricted | full
cpu_limit = "2"
memory_limit = "4g"
timeout_seconds = 120
keep_alive = false            # reuse a named container across runs (enables sandbox shell)
protect_secret_files = true   # refuse agent writes to id_rsa/*.pem/.ssh, etc.

[policy]
require_approval_for = ["git push", "rm -rf", "npm publish", "pip install", "curl"]
deny = ["shutdown", "reboot", "mkfs", "dd if="]

[subagents.models]            # optional per-role model overrides
coder = "groq:llama-3.3-70b-versatile"
tester = "ollama:qwen2.5-coder:7b"

[telemetry]
enabled = false               # opt-in, local-only counts; never uploaded
```

---

## 9. Sandbox escape hatch (debugging)

```bash
# only for keep-alive sessions (set sandbox.keep_alive = true)
uv run autodev sandbox shell <session_id>   # raw shell inside the container
```

---

## 10. Dev tasks

```bash
uv run pytest                 # run tests
uv run ruff check src tests   # lint
uv run ruff format src tests  # format
uv build                      # build the wheel/sdist
```

---

## 11. Quick reference

| Command | Purpose |
|---|---|
| `autodev init` | First-run wizard |
| `autodev doctor` | Health + capability check |
| `autodev start [PROMPT]` | Interactive REPL |
| `autodev run "<task>" --yes` | One-shot (CI) |
| `autodev resume <id>` | Resume a session |
| `autodev sessions list` | List past sessions |
| `autodev models list / pull` | Model management |
| `autodev config get/set/edit` | Config management |
| `autodev config telemetry` | Show local opt-in usage counters |
| `autodev sandbox shell <id>` | Raw container shell (keep-alive sessions) |
