# OpenLocal CLI — Full Redesign & Implementation Blueprint

A local-first, provider-agnostic, open-source coding agent CLI. Runs against **Ollama**, **llama.cpp**, or **Groq** (bring-your-own-key), with a sandboxed execution environment built on **deepagents** (LangChain's agent harness).

This document supersedes the original draft. The core change: **the model layer is now a pluggable provider system**, not a single hard-coded Ollama call — and the sandbox, config, and safety systems are designed around the reality that users will mix local and cloud models in the same project.

---

## 1. Design Goals & Non-Negotiables

1. **Provider-agnostic by default.** Ollama, llama.cpp (via its OpenAI-compatible server), and Groq must be first-class, swappable at runtime — not just at install time.
2. **Local-first, cloud-optional.** The tool must fully function with zero API keys and zero internet access (Ollama/llama.cpp only). Groq is opt-in and clearly flagged as "your code leaves this machine."
3. **Safety before capability.** Shell execution, file writes, and network access are sandboxed and permissioned. A model hallucinating `rm -rf /` should never be able to touch the host.
4. **Resumable, not fire-and-forget.** Long tasks must survive a crashed terminal, a killed container, or a laptop sleep cycle.
5. **Small-model-aware.** Local 4B–8B models are the common case, not the exception. The harness must degrade gracefully when tool-calling is unreliable, context is small, and the model loses track of multi-step plans.
6. **Boring, auditable packaging.** Any contributor should be able to read the repo in an afternoon.

---

## 2. Stack (Final)

| Layer | Choice | Why |
|---|---|---|
| CLI framework | `typer` (+ `rich` for output) | Typed commands, auto `--help`, good DX |
| Agent harness | `deepagents` (LangChain) | Gives you `create_deep_agent`, virtual filesystem, subagent `task` tool, `write_todos` planning, memory middleware — out of the box |
| Model abstraction | `langchain.chat_models.init_chat_model("provider:model")` | LangChain's own provider-string convention (`ollama:llama3.1`, `groq:llama-3.3-70b-versatile`, `openai:...` shape for llama.cpp's OpenAI-compatible server) — no custom wrapper needed for most cases |
| Local inference (weights) | `ollama` (daemon + Python client) | Simplest local model management (`ollama pull`, `ollama list`) |
| Local inference (raw GGUF) | `llama.cpp` server (`llama-server`) exposing an OpenAI-compatible `/v1/chat/completions` endpoint | For users who want GGUF files without Ollama's model format/daemon overhead |
| Cloud fallback | `groq` via `langchain-groq` (`ChatGroq`) | Fast hosted inference for when local hardware is too slow; strictly BYO API key |
| Sandbox | Docker Python SDK, implementing `SandboxBackendProtocol` | Isolated execution, resource limits, network policy |
| Secrets | `keyring` (OS credential store) with `.env`/config-file fallback | Never store Groq keys in plaintext by default |
| Packaging | `pyproject.toml` via `uv` or `poetry`, published to PyPI as `openlocal-cli`, installed with `pipx` | Standard, isolated global install |
| Persistence | LangGraph checkpointer (SQLite file per project, `.openlocal/state.db`) | Resumable sessions, time-travel/undo |

---

## 3. Repository Layout (Expanded)

```
openlocal-cli/
├── pyproject.toml
├── README.md
├── LICENSE
├── SECURITY.md                     # Threat model + responsible disclosure
├── CONTRIBUTING.md
├── docker/
│   ├── base-python.Dockerfile      # python:3.12-slim + common tooling
│   ├── base-node.Dockerfile        # node:20-slim + common tooling
│   ├── base-polyglot.Dockerfile    # both, for full-stack repos
│   └── entrypoint.sh               # drops privileges, sets up non-root user
├── openlocal/
│   ├── __init__.py
│   ├── cli.py                      # Typer app, command definitions only
│   ├── config.py                   # Layered config resolution (see 5)
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py                 # ProviderSpec dataclass, registry
│   │   ├── ollama_provider.py      # health check, model pull/list helpers
│   │   ├── llamacpp_provider.py    # server lifecycle mgmt (start/stop llama-server)
│   │   └── groq_provider.py        # key validation, rate-limit/cost tracking
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── build.py                # create_deep_agent(...) wiring
│   │   ├── subagents.py            # planner / coder / tester / reviewer specs
│   │   ├── prompts/
│   │   │   ├── system.md
│   │   │   ├── planner.md
│   │   │   ├── coder.md
│   │   │   ├── tester.md
│   │   │   └── reviewer.md
│   │   └── memory.py               # AGENTS.md loading, session summarization
│   ├── sandbox/
│   │   ├── __init__.py
│   │   ├── docker_backend.py       # SandboxBackendProtocol implementation
│   │   ├── policy.py               # command allow/deny lists, approval rules
│   │   ├── secret_scan.py          # pre-flight scan before cloud calls
│   │   └── image_select.py         # detects repo type -> picks base image
│   ├── ui/
│   │   ├── console.py              # rich rendering: diffs, spinners, tool logs
│   │   └── approval.py             # interactive y/n prompts for risky actions
│   └── telemetry.py                # opt-in, local-only usage stats (off by default)
└── tests/
    ├── test_cli.py
    ├── test_providers.py
    ├── test_sandbox.py
    ├── test_agent_build.py
    ├── test_secret_scan.py
    └── fixtures/
        ├── sample_flask_repo/
        ├── sample_react_repo/
        └── sample_spring_repo/
```

---

## 4. The Provider Layer (the actual redesign)

The original draft hard-wired Ollama. That breaks the moment someone wants Groq for a hard refactor and Ollama for cheap boilerplate. Fix: a **provider registry** with a uniform interface, resolved from config + CLI flags, with runtime switching mid-session.

### 4.1 `ProviderSpec`

```python
# openlocal/providers/base.py
from dataclasses import dataclass

@dataclass
class ProviderSpec:
    name: str                        # "ollama" | "llamacpp" | "groq"
    model_string: str                # LangChain provider string, e.g. "ollama:llama3.1"
    requires_api_key: bool
    is_local: bool
    context_window: int              # used for context-budgeting decisions
    supports_tool_calling: bool       # some small local models don't reliably
```

### 4.2 Resolution order

1. `--model` CLI flag (explicit override, always wins): `openlocal start --model groq:llama-3.3-70b-versatile`
2. Project config `.openlocal.toml` -> `[model] default = "ollama:qwen2.5-coder:7b"`
3. Global config `~/.openlocal/config.toml`
4. Interactive first-run wizard (`openlocal init`) that detects what's actually available:
   - Pings `http://localhost:11434` for Ollama; if up, lists installed models via `ollama list`.
   - Checks for a running `llama-server` on the configured port, or offers to launch one from a GGUF path.
   - Asks "Do you want to configure a Groq API key for cloud fallback? (y/N)" — stored via `keyring`, never echoed, never committed (auto-adds `.openlocal/` to `.gitignore`).

### 4.3 Runtime model switching

`deepagents`' `create_deep_agent(model=...)` accepts either a string or a chat model instance. Because `init_chat_model` already understands `ollama:` and `groq:` provider strings, and llama.cpp's server speaks the OpenAI API shape, the CLI can hot-swap models between turns without changing agent code:

```python
from langchain.chat_models import init_chat_model

def resolve_model(spec: ProviderSpec):
    if spec.name == "llamacpp":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            base_url="http://localhost:8080/v1",
            api_key="not-needed",       # llama-server ignores this
            model=spec.model_string,
        )
    return init_chat_model(model=spec.model_string)  # "ollama:..." or "groq:..."
```

A `/model` slash command inside the interactive REPL lets the user swap providers mid-conversation, e.g.:

```
> /model groq:llama-3.3-70b-versatile
Switched to Groq (cloud). Local file contents will now be sent to Groq's API.
```

**Critical UX rule:** switching *into* a cloud provider always prints a one-line data-leaves-your-machine warning before the next tool call executes. Switching *back* to a local provider is silent.

### 4.4 Fallback chain (optional, opt-in)

Some users want "try local first, fall back to Groq if local fails or times out" for slow hardware. Config:

```toml
[model]
primary = "ollama:qwen2.5-coder:7b"
fallback = "groq:llama-3.3-70b-versatile"
fallback_on = ["timeout", "tool_call_parse_error"]
fallback_timeout_seconds = 45
```

Implemented as a thin wrapper chat model that catches specific exceptions/timeouts and retries once against the fallback, logging the switch visibly — never silently, since it has cost and privacy implications.

### 4.5 Handling unreliable tool-calling on small local models

Real edge case: a 4B–8B local model frequently emits malformed tool calls or ignores tools entirely. Mitigations:

- **Capability probing at startup**: `openlocal doctor` runs a scripted tool-call test against the configured model and records `supports_tool_calling` in the resolved `ProviderSpec`. If it fails, the CLI warns and suggests either a bigger local model, a model known to be tool-call-tuned (e.g. Qwen2.5-Coder, Llama-3.1-instruct), or Groq.
- **Structured retry**: if a tool call fails to parse, re-prompt with the exact schema error once before surfacing failure to the user, rather than silently giving up.
- **Reduced tool surface for small models**: exclude noisier built-ins (e.g. `task` subagent delegation) for models below a configurable context/parameter threshold, since spawning subagents compounds tool-calling failures.

---

## 5. Configuration System

Three layers, later overrides earlier:

1. **Global** — `~/.openlocal/config.toml`: default provider, telemetry opt-in, keyring backend choice, default sandbox image.
2. **Project** — `<repo>/.openlocal.toml` (committed, no secrets): preferred model, allowed shell commands, container resource limits, which subagents are enabled. This is what makes a repo's agent behavior reproducible for teammates.
3. **Session/CLI flags** — `--model`, `--no-network`, `--yolo` (skip approval prompts — must require an explicit flag, never a default).

Example `.openlocal.toml`:

```toml
[model]
default = "ollama:qwen2.5-coder:7b"

[sandbox]
image = "openlocal/base-node:20"
network = "none"          # "none" | "restricted" | "full"
cpu_limit = "2"
memory_limit = "4g"
timeout_seconds = 120

[policy]
require_approval_for = ["git push", "rm -rf", "npm publish", "curl", "pip install"]
deny = ["shutdown", "reboot", "mkfs", "dd if="]

[subagents]
enable_task_delegation = true
max_concurrent = 3
```

Secrets (Groq API key) are **never** stored in `.openlocal.toml`. They live in the OS keyring, or as an env var (`GROQ_API_KEY`) for CI/headless use, resolved in that priority order.

---

## 6. CLI Command Surface

```
openlocal init                  # interactive first-run: detect providers, write config
openlocal doctor                 # health check: docker daemon, ollama reachability,
                                #   llama-server reachability, groq key validity,
                                #   tool-calling capability probe
openlocal models list             # unified view across ollama/llama.cpp/groq
openlocal models pull <name>      # proxies to `ollama pull` when provider=ollama
openlocal start [PROMPT]          # main entry: launches sandbox + agent REPL
openlocal run "<task>" --yes      # non-interactive one-shot mode (for CI/scripts)
openlocal resume <session_id>     # reattach to a previous checkpointed session
openlocal sessions list           # show past sessions with status (done/failed/paused)
openlocal sandbox shell           # drop into a raw shell inside the running container
                                 #   (manual escape hatch for debugging)
openlocal config get/set <key>    # read/write layered config
openlocal config edit             # opens config in $EDITOR
```

**Why `run` exists separately from `start`:** interactive REPL vs. scriptable non-interactive execution are genuinely different use cases (a human iterating vs. a CI pipeline calling `openlocal run "add null check" --yes --model groq:...`). Conflating them into one command with flags gets confusing fast.

---

## 7. The Sandbox — Full Design

### 7.1 Why Docker (not the host, not just a venv)

The model can and will hallucinate destructive commands. A subprocess on the host with the user's real permissions is not an acceptable execution surface, full stop. Docker gives filesystem, process, and (optionally) network isolation with mature tooling.

### 7.2 Implementing `SandboxBackendProtocol`

deepagents' sandbox contract requires, at minimum, virtual filesystem operations (`read_file`, `write_file`, `edit_file`, `ls`) and an `execute(command)` method. When the harness detects a backend implementing this protocol, it automatically exposes the `execute` tool to the model — no manual tool registration needed. Passing the backend via `create_deep_agent(backend=...)` is all that's required.

```python
# openlocal/sandbox/docker_backend.py (shape, not full impl)
class DockerSandboxBackend:
    def __init__(self, workdir: str, image: str, network: str, limits: dict):
        self.container = self._start_container(workdir, image, network, limits)

    def read_file(self, path: str) -> str: ...
    def write_file(self, path: str, content: str) -> None: ...
    def edit_file(self, path: str, old: str, new: str) -> None: ...
    def ls(self, path: str = ".") -> list[str]: ...

    def execute(self, command: str, timeout: int | None = None):
        # 1. Check policy.py: deny-list match -> refuse immediately, no container call
        # 2. Check policy.py: approval-required match -> prompt user via ui/approval.py
        # 3. docker exec into the running container, capture stdout/stderr/exit_code
        # 4. Truncate output to a token-safe size before returning to the agent
        ...
```

### 7.3 Container lifecycle

1. **Image selection** (`image_select.py`): inspect the repo root for `package.json`, `requirements.txt`/`pyproject.toml`, `pom.xml`/`build.gradle`, `go.mod`, etc., and pick (or let the user override in `.openlocal.toml`) the closest base image. Polyglot repos (e.g. React frontend + Spring Boot backend) get the `base-polyglot` image or, better, **two containers** networked together via a Compose-style setup — this matters more than the original draft acknowledged, because "find the User entity and add a column" in a Spring Boot repo needs a JDK + Maven/Gradle, not `python:3.10-slim`.
2. **Mount strategy**: bind-mount the repo at `/workspace`, read-write, but:
   - `.git/` is mounted but commits/pushes require the `require_approval_for` policy hit.
   - Anything matching secret-shaped patterns (`.env`, `*.pem`, `id_rsa*`) is mounted read-only or excluded entirely, configurable.
3. **Non-root execution**: container runs as an unprivileged user (`entrypoint.sh` maps to the host UID on Linux, or a fixed sandbox UID on macOS/Windows where UID mapping is less meaningful) so files written by the container are owned correctly on the host and the container can't do host-level privileged operations even if it escaped.
4. **Resource limits**: memory, CPU, and process-count limits set via the Docker SDK to prevent a runaway fork bomb from taking down the host.
5. **Network policy** (`network` in config):
   - `none` (default): container has no network access at all — safest, and the only sane default when using local models for privacy-sensitive repos.
   - `restricted`: only the provider's endpoint is reachable (e.g. allow `host.docker.internal:11434` for Ollama running on the host, or Groq's API host) — needed because the **agent's tool calls** run inside the container but the **model inference** usually happens outside it (Ollama/llama.cpp on the host, or Groq over the internet). This is a subtlety the original draft missed entirely: **the sandbox isolates code execution, not model inference** — the LLM call happens from the CLI process on the host, not from inside the container.
   - `full`: for tasks that legitimately need `npm install`, `pip install`, `go get`, etc. — opt-in, with the deny-list still enforced.
6. **Teardown**: containers are ephemeral per-session by default (`openlocal start` creates one, `resume` can restart a stopped one from the same image+mounts). A `--keep-alive` flag persists it as a named container for faster iterative dev.

### 7.4 Feedback loop

`stdout`/`stderr`/`exit_code` from `execute()` go straight back into the agent's message stream. Two refinements over the original draft:
- **Truncate aggressively** (e.g. last few KB of stderr/stdout) before returning to the model — full CI logs will blow a small model's context window instantly. Store the untruncated output in `/workspace/.openlocal/logs/` and tell the agent the path so it can `read_file` more if needed.
- **Structured exit signaling**: wrap the raw exec result in a small JSON envelope (`exit_code`, `stdout`, `stderr`, `truncated`, `log_path`) so the model can reliably branch on success/failure instead of parsing prose.

### 7.5 Sandbox edge cases specifically designed for

- **Large monorepos**: bind-mounting a large repo is fine (no copy), but `ls`/tree operations must be depth-limited and `.gitignore`/`node_modules`/`.venv`-aware by default, or the agent's first recursive listing floods its own context.
- **Binary files**: `read_file` must detect non-text content (images, compiled artifacts) and refuse with a clear message rather than dumping garbage bytes into the model's context.
- **Long-running dev servers**: if the agent runs a blocking command like a dev server, `execute()` needs a hard timeout (config default ~120s) that kills the process tree and returns partial output, rather than hanging the session forever.
- **Secrets in the repo**: before any tool-call payload or file content is sent to a **cloud** provider (Groq), run `secret_scan.py` (regex + entropy heuristics for API keys, private keys, `.env` values) and redact matches, warning the user in the console. This scan is skipped for local providers since nothing leaves the machine — this distinction should be visible in the UI, not just a code comment.
- **Windows/Docker Desktop quirks**: file-permission and path-translation issues (`/workspace` vs `C:\...`) are common; `openlocal doctor` should explicitly test a write-then-read round trip and surface WSL2-backend-specific errors with actionable messages instead of raw Docker SDK tracebacks.
- **Apple Silicon / GPU passthrough**: Docker on macOS can't pass through the GPU, so Ollama should generally run on the host (already the recommended pattern above — the container never runs the model, only the code), avoiding the whole "GPU inside container" problem entirely.

---

## 8. Agent Architecture (`agent/build.py`)

```python
from deepagents import create_deep_agent
from openlocal.sandbox.docker_backend import DockerSandboxBackend
from openlocal.providers.base import resolve_model

def build_agent(spec, sandbox: DockerSandboxBackend, config: dict):
    model = resolve_model(spec)
    return create_deep_agent(
        model=model,
        backend=sandbox,                     # -> harness auto-exposes `execute` tool
        system_prompt=load_prompt("system.md"),
        subagents=[
            planner_subagent,                 # decomposes task into todos, no exec access
            coder_subagent,                    # write_file/edit_file + execute
            tester_subagent,                   # execute-only, runs test suites
            reviewer_subagent,                 # read-only, diff review + risk flags
        ] if config["subagents"]["enable_task_delegation"] else [],
        interrupt_on={
            "execute": True,                    # gated further by policy.py at call time
        },
    )
```

### 8.1 Subagent split (rationale)

Rather than one monolithic agent doing planning + coding + testing + reviewing in one context, splitting into subagents (deepagents' `task` tool) keeps each context window focused and lets you swap models per-role — e.g. use a small fast local model for the `tester` subagent (just runs the test suite and reports pass/fail) while reserving Groq's larger model for the `coder` subagent doing the actual multi-file edit. This is configurable per-project in `.openlocal.toml`:

```toml
[subagents.models]
planner = "ollama:qwen2.5-coder:7b"
coder = "groq:llama-3.3-70b-versatile"
tester = "ollama:qwen2.5-coder:7b"
reviewer = "ollama:qwen2.5-coder:7b"
```

### 8.2 Memory & project conventions

Support an `AGENTS.md` file at the repo root (deepagents' memory middleware already looks for this convention) where users document project-specific rules ("always run the formatter before finishing", "never touch `/migrations`", "tests live in `tests/`"). This is loaded into the system prompt automatically and is the primary mechanism for teaching the agent house rules without editing CLI code.

### 8.3 Human-in-the-loop approval

`interrupt_on={"execute": True}` pauses the graph before shell commands run; `policy.py` decides, per command, whether that pause needs a real prompt (approval-list match), an outright refusal (deny-list match), or silent pass-through (allow-listed/benign). Risky commands render a diff/command preview with `[y/N/always-allow-this-session]` via `ui/approval.py`. This is the single most important safety feature in the whole system and must never be bypassable except by the explicit `--yolo` flag.

---

## 9. Safety, Privacy & Threat Model (`SECURITY.md`)

- **Deny-list is absolute**: commands matching `policy.deny` (e.g. disk-formatting commands, fork bombs) are refused before ever reaching Docker, regardless of approval — no override.
- **Approval-list is the default safety net**: git pushes, package publishes, destructive deletes, and outbound network calls require interactive confirmation unless `--yolo`.
- **Cloud vs local data flow must be visible in the UI at all times** — a persistent status line (rich footer) showing the active model and whether it's `[local]` or `[cloud: groq]`.
- **No telemetry by default.** If a user opts in, only anonymous command-usage counts are collected locally and never auto-uploaded — publish exactly what's collected in `SECURITY.md`, don't make people trust a claim.
- **Prompt injection from repo content**: a malicious README or code comment could try to instruct the agent to exfiltrate secrets or run dangerous commands. Mitigation: file contents read via `read_file` are tagged as *data*, not *instructions*, in the system prompt, and the approval gate on `execute` is the real backstop regardless of what convinces the model to try.

---

## 10. Resumability & Session Management

Each `openlocal start` session gets a UUID and a LangGraph SQLite checkpointer at `.openlocal/sessions/<uuid>.db`. This gives you, for free:
- **Resume after crash/Ctrl-C**: `openlocal resume <uuid>` reloads the exact graph state, todos, and message history, and reattaches to (or restarts) the same container.
- **Undo/branch**: since checkpoints are addressable, a future `openlocal rewind <uuid> --to-step N` is a natural extension — worth designing the state schema with this in mind now even if not built in v1.
- **CI mode cleanliness**: `openlocal run` (non-interactive) still writes a checkpoint so a failed CI run's session can be inspected locally afterward with `openlocal resume`.

---

## 11. Testing Strategy (Expanded)

Beyond the original draft's happy-path suggestion, deliberately test:

1. **Polyglot repo fixtures**: a small Spring Boot app, a React app, a Flask app checked into `tests/fixtures/` so `image_select.py` and cross-container setups are actually exercised in CI, not just described in docs.
2. **Small-model tool-calling flakiness**: a fixture test that feeds deliberately malformed tool-call output through the harness's retry path to confirm it recovers rather than crashing.
3. **Policy enforcement tests**: assert that deny-listed commands never reach `docker exec` (mock the Docker client and assert it was never called), and that approval-listed commands pause execution and wait on the approval callback.
4. **Secret redaction tests**: seed a fixture repo with a fake `.env` containing a fake API-key-shaped string, run a task that would read it, and assert it's redacted before any Groq-bound payload is constructed (mock the Groq call and inspect the request body).
5. **Timeout/kill tests**: start a long-running/blocking command and assert `execute()` returns within the configured timeout with `truncated: true`.
6. **Resumability tests**: kill the process mid-session and confirm `openlocal resume` reconstructs identical state.

---

## 12. Packaging & Distribution

- `pyproject.toml` with **optional extras** so the base install stays light:
  ```toml
  [project.optional-dependencies]
  groq = ["langchain-groq"]
  llamacpp = ["langchain-openai"]   # reused, since llama.cpp server is OpenAI-shaped
  all = ["openlocal-cli[groq,llamacpp]"]
  ```
  `pipx install openlocal-cli` gets Ollama support by default (via `langchain-ollama`); `pipx install "openlocal-cli[all]"` gets everything.
- **Docker base images** published to a registry (GHCR) under versioned tags so `openlocal start` doesn't rebuild images from scratch on every fresh machine.
- **CI** (GitHub Actions): lint (`ruff`), type-check (`mypy`), unit tests, and a slower "integration" job that spins up Docker + a tiny local model (or a mocked provider) to exercise the sandbox end-to-end.
- **README** must cover, explicitly: installing Docker, installing Ollama *or* llama.cpp *or* getting a Groq key, the `openlocal init` wizard, and a "which provider should I pick" decision table (roughly: Ollama for ease of local use, llama.cpp for raw GGUF/performance tuning, Groq when local hardware is too slow or the task needs a bigger model than fits on your machine).
- **Versioning**: SemVer; sandbox protocol changes (which will happen as `deepagents` itself evolves) are called out explicitly in changelogs since they can silently break custom backends for contributors who forked `docker_backend.py`.

---

## 13. Extensibility (Plugin Points)

Design these as entry points from day one so the project doesn't need a rewrite to add, e.g., vLLM or LM Studio support later:

- **New model providers**: register via Python entry points (`openlocal.providers`) implementing `ProviderSpec` + a `resolve()` function — third parties can `pip install openlocal-provider-vllm` without touching core.
- **New sandbox backends**: anything implementing `SandboxBackendProtocol` can replace `DockerSandboxBackend` (e.g. a future Podman backend, or a cloud sandbox for users who don't want Docker locally at all — deepagents already ships partner integrations along these lines).
- **New subagents**: declarative subagent specs in `.openlocal.toml` mean users can add a `security-reviewer` subagent without a code change.

---

## 14. Open Design Questions (call these out to contributors, don't silently pick)

- Should `.openlocal.toml` be committed to the user's repo by default, or opt-in? (Reproducibility vs. not polluting unrelated repos.)
- How aggressive should the deny-list be by default — a strict allow-list model (safer, more setup friction) vs. today's deny-list model (easier onboarding, relies on the list being comprehensive)?
- Should Groq usage/rate-limit tracking be surfaced in the CLI footer, given Groq's pricing and limits change independently of this project?

---

This blueprint intentionally leaves out day/week estimates — sequence the work as: **provider layer stub + config -> sandbox with policy enforcement -> agent wiring with one subagent -> full subagent split + resumability -> packaging.** Each step should be independently demoable.
