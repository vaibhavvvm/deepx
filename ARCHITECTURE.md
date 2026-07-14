# OpenLocal CLI — Architecture & File-by-File Logic

A local-first, provider-agnostic, sandboxed coding-agent CLI built on
`deepagents` (LangChain's agent harness). This document explains **what each
file does and why** — the mental model you need before touching the code.

For setup and run commands only, see [`COMMANDS.md`](./COMMANDS.md).

---

## 1. The big picture

```
                ┌──────────────────────────────────────────────┐
   you type ──▶ │ cli.py  (Typer command surface)              │
                └──────────────┬───────────────────────────────┘
                               │ builds
             ┌─────────────────┼──────────────────────────┐
             ▼                 ▼                          ▼
      config.py          providers/*             sandbox/docker_backend.py
   (layered config)   (model → BaseChatModel)   (Docker exec, policy-gated)
             │                 │                          │
             └────────┬────────┴──────────┬───────────────┘
                      ▼                   ▼
                 agent/build.py  ──▶  create_deep_agent(model, backend, ...)
                      │                   │
                      ▼                   ▼
                 runner.py           session.py (SQLite checkpointer, resume)
              (stream + render)
```

**Core idea:** the CLI resolves a *config* → picks a *provider/model* → starts a
*Docker sandbox* → wires them into a *deepagents* agent → drives it through the
*runner*, checkpointing every step to *SQLite* so any session is resumable.

Three invariants hold no matter what the model does:

1. **Deny-list is absolute** — enforced inside the sandbox backend, before Docker.
2. **Approval gate** — risky commands need a human `y`, unless `--yolo`.
3. **Cloud data is scrubbed** — secrets redacted before any cloud model call;
   local models get no scan because nothing leaves the machine.

---

## 2. Package layout

```
src/openlocal/
├── __init__.py            # version
├── cli.py                 # Typer app — command definitions only
├── config.py              # layered config resolution (global→project→CLI)
├── session.py             # session UUIDs, SQLite checkpointer, resume, .gitignore
├── runner.py              # drive the agent: stream steps, render tool activity
├── doctor.py              # health checks + tool-calling capability probe
├── telemetry.py           # opt-in, local-only usage counters (off by default)
├── providers/
│   ├── base.py            # ProviderSpec, Provider registry, resolve_model
│   ├── ollama_provider.py # local Ollama daemon
│   ├── llamacpp_provider.py # local llama-server (OpenAI-shaped)
│   └── groq_provider.py   # cloud Groq (BYO key, keyring)
├── sandbox/
│   ├── policy.py          # deny / approve / allow classification
│   ├── secret_scan.py     # regex + entropy redaction for cloud payloads
│   ├── image_select.py    # repo-type detection → base image
│   └── docker_backend.py  # SandboxBackendProtocol impl (robust: reconnect/guards)
├── agent/
│   ├── build.py           # create_deep_agent(...) wiring + middleware stack
│   ├── subagents.py       # planner/coder/tester/reviewer specs
│   ├── memory.py          # AGENTS.md → system prompt (as tagged data)
│   ├── redaction.py       # middleware: scrub secrets from cloud model calls
│   ├── fallback.py        # middleware: local→cloud fallback on timeout/error
│   ├── retry.py           # middleware: re-prompt once on malformed tool calls
│   └── prompts/*.md       # system + per-subagent prompts
├── ui/
│   ├── console.py         # rich rendering: banners, status line, tables
│   └── approval.py        # interactive y/n/always approval callback
└── commands/
    ├── init_cmd.py        # `openlocal init` wizard
    ├── run_cmd.py         # `start` / `run` / `resume` orchestration
    └── repl.py            # interactive REPL + slash commands (opencode-style)

docker/                    # base images: python/node/java/go/polyglot + entrypoint.sh
tests/                     # pytest suite (Docker backend mocked — no daemon needed)
```

---

## 3. File-by-file logic

### Entry point

**`cli.py`** — The Typer app. Purely declarative: defines commands (`init`,
`doctor`, `start`, `run`, `resume`, `models`, `config`, `sessions`, `sandbox`)
and their flags, then delegates to `commands/*`. Keeps heavy imports lazy (inside
functions) so `openlocal --help` stays instant. `_resolve_spec` turns a
`provider:model` string into a `ProviderSpec`; `_network_overrides` maps
`--network`/`--no-network` flags into the session config layer.

### Configuration

**`config.py`** — Three-layer config, later overrides earlier:
`DEFAULTS` → `~/.openlocal/config.toml` (global) → `<repo>/.openlocal.toml` (project)
→ CLI-flag overrides. `find_project_root` walks up looking for `.openlocal.toml`
then `.git`. `load_config` deep-merges all layers into a `Config` object with
typed accessors (`.model_string`, `.sandbox`, `.policy`, `.subagents`).
`set_key` writes a dotted `section.key` into the chosen scope's TOML.
**Secrets never live here** — only in the keyring / env.

### Providers (the pluggable model layer)

**`providers/base.py`** — The heart of the redesign. `ProviderSpec` is the
currency the whole system trades in: `name`, `model`, `is_local`,
`requires_api_key`, `context_window`, `supports_tool_calling`. A `Provider`
registry lets each backend describe itself, health-check, and produce a
LangChain `BaseChatModel`. `parse_model_string` splits `ollama:qwen2.5-coder:7b`
on the *first* colon (model names contain colons). `resolve_model(spec)` is the
one call the agent builder uses. Third-party providers can register via the
`openlocal.providers` entry point without touching core.

**`providers/ollama_provider.py`** — Local Ollama. `build_spec` guesses
tool-calling support from a known-good family list (Qwen2.5-Coder, Llama-3.1,
etc.) and picks a context window hint. `resolve` returns `ChatOllama` with
`num_ctx` set so small models use their full window. `health`/`list_models`/
`pull` proxy the Ollama HTTP API.

**`providers/llamacpp_provider.py`** — Raw GGUF via `llama-server`. Reuses
`ChatOpenAI` pointed at the local OpenAI-shaped endpoint (`/v1`). API key is a
dummy the server ignores. Optional extra (`langchain-openai`).

**`providers/groq_provider.py`** — Cloud Groq, BYO key. `get_api_key` resolves
from **keyring first, then `GROQ_API_KEY`** — never from config files.
`set_api_key` stores in the OS keyring. `health` validates the key against
Groq's `/models`. Optional extra (`langchain-groq`).

### Sandbox (safe execution)

**`sandbox/policy.py`** — Classifies every command as `DENY` / `APPROVE` /
`ALLOW`. Deny wins over approve. Matching is whitespace-normalized and
space-stripped so obfuscated spacing (`:(){:|:&};:`) still trips rules.
`evaluate_pipeline` splits compound commands on shell operators (`|`, `&&`, `;`,
`>`, backticks) so a benign prefix can't smuggle a risky suffix past the gate.

**`sandbox/secret_scan.py`** — Redacts secret-shaped strings before any
cloud-bound payload. Combines high-signal regexes (AWS/Groq/OpenAI/GitHub keys,
PEM blocks, `KEY=`/`TOKEN=` env assignments, bearer tokens) with a **Shannon-
entropy** heuristic for opaque high-entropy tokens. Findings carry only a short,
non-reversible preview (`abc…yz`) so the UI can report without leaking.

**`sandbox/image_select.py`** — Detects repo stack from marker files
(`package.json`→node, `pom.xml`→java, `go.mod`→go, `pyproject.toml`→python).
Single stack → that base image; multiple → polyglot. Each image maps to a
Docker Hub **fallback** so a fresh machine works before project images publish.

**`sandbox/docker_backend.py`** — The execution surface. Subclasses deepagents'
`BaseSandbox`, implementing only four primitives — `execute`, `id`,
`upload_files`, `download_files` — and gets `ls`/`read`/`write`/`edit`/`grep`
for free (they shell out via `execute`). Key logic:
- Container runs **unprivileged** (`user=host-uid`, `cap_drop=ALL`,
  `no-new-privileges`), with CPU/memory/PID limits, repo bind-mounted at
  `/workspace`.
- Network policy: `none` (no net, the default — inference runs on the *host*,
  not the container), `restricted` (host-gateway reachable), `full`.
- `execute`: policy check **first** — deny → refuse without ever calling Docker;
  approve → invoke the approval callback. Then runs via coreutils `timeout` so a
  runaway/dev-server command is killed and returns partial output
  (`truncated=True`). Output over ~16 KB is tailed and the full log written to
  `/workspace/.openlocal/logs/` for the agent to `read_file`.
- `upload_files`/`download_files` stream bytes via tar (`put_archive`/
  `get_archive`).
- **Robustness:** `client` raises a clear `SandboxUnavailable` if the daemon is
  down (instead of a raw SDK traceback); `_ensure_running` reloads and restarts a
  container that crashed/exited/vanished between turns; `_raw_exec` retries once
  through a reconnect on a `NotFound`/`APIError`; image-pull failure is reported
  actionably. `upload_files` refuses writes to secret-shaped paths (`id_rsa`,
  `*.pem`, `~/.ssh/`, …) via `protect_secret_files`.

### Agent (deepagents wiring)

**`agent/build.py`** — `build_agent` assembles the compiled agent:
`resolve_model(spec)` + Docker `backend` + composed system prompt +
subagents (only if the model supports tool-calling **and** its context ≥ 16 K —
small models get a trimmed tool surface) + SQLite `checkpointer`. `load_prompt`
reads bundled markdown. The **middleware stack** is ordered outermost-first:
1. **redaction** (cloud specs only) — scrub secrets before anything else;
2. **fallback** (if `[model] fallback` set) — swap model on timeout/error;
3. **retry** (small/unreliable models) — re-prompt once on a malformed tool call.

**`agent/subagents.py`** — Builds `planner` / `coder` / `tester` / `reviewer`
`SubAgent` specs and applies per-role model overrides from
`.openlocal.toml [subagents.models]` (e.g. cheap local tester, Groq coder).

**`agent/memory.py`** — Loads the repo's `AGENTS.md` and folds it into the
system prompt wrapped in `<project_conventions>` tags — framed as *data/config*,
not instructions, to blunt prompt injection from a hostile repo.

**`agent/redaction.py`** — `CloudRedactionMiddleware` implements deepagents'
`wrap_model_call` hook. For cloud models only, it scans every outgoing message,
redacts secrets via `secret_scan`, warns the user, and forwards the scrubbed
request. Not installed at all for local models → zero overhead, real distinction.

**`agent/fallback.py`** — `FallbackMiddleware` (opt-in via `[model] fallback`).
Wraps the model call: on a matching error, or when the primary exceeds
`fallback_timeout_seconds` (run in a worker thread with a wall clock), it retries
once against the fallback model. The switch is always logged, and a cloud
fallback triggers the privacy banner — never silent.

**`agent/retry.py`** — `ToolCallRetryMiddleware`. When a small model emits an
unparseable tool call (`AIMessage.invalid_tool_calls`), it re-prompts once with
the exact schema error instead of dead-ending the turn. Installed only for
small/unreliable models.

**`agent/prompts/*.md`** — `system.md` (main agent: understand→minimal change→
verify, treat file content as untrusted data, respect the sandbox) plus focused
prompts for each subagent.

### UI

**`ui/console.py`** — All rich rendering: `banner`, `status_line` (persistent
`model … [local]/[cloud: groq] net none` footer), `cloud_switch_warning` (the
data-leaves-your-machine banner), `doctor_table`, `secret_findings`, tool logs.

**`ui/approval.py`** — `ApprovalController` is the approval callback the sandbox
calls. Renders the command, asks `y/n/a`; `a` = allow this rule for the rest of
the session. `auto_approve` is the non-interactive `--yes`/CI variant. Deny-list
is still enforced in the backend regardless.

### Sessions & running

**`session.py`** — Each session gets a 12-char UUID, a JSON metadata record
(model, status, prompt, image, network) and a SQLite checkpointer at
`.openlocal/sessions/<id>.db`. `open_checkpointer` yields a `SqliteSaver`;
`thread_config` selects the thread. `ensure_gitignore` adds `.openlocal/`.
History is never mutated in place → lays groundwork for a future `rewind`.

**`runner.py`** — Drives one user turn: streams the agent (`stream_mode=values`),
de-dupes messages by id, renders AI text + tool calls + short tool-result
traces, returns the final assistant text.

**`doctor.py`** — Health checks: Docker daemon ping, a write-then-read Docker
round-trip (catches Docker Desktop / WSL2 path issues), provider reachability,
and a **tool-calling probe** — binds a trivial `add` tool and checks the model
emits a valid tool call, recording capability empirically.

**`commands/init_cmd.py`** — `openlocal init` wizard: detect Ollama models, offer
llama.cpp/Groq, pick default model + network policy, write `.openlocal.toml`,
store any Groq key in the keyring, add `.gitignore`.

**`commands/run_cmd.py`** — Orchestrates `start`/`run`/`resume`. `_build_sandbox`
assembles the Docker backend from config; `_preflight` warns on unreachable
providers / weak tool-calling; `_run_loop` starts the container, builds the
agent inside the checkpointer context, runs the initial prompt and/or hands off
to the REPL, and always tears the container down (on Ctrl-C it marks the session
`paused` and prints the resume command).

**`commands/repl.py`** — The `Repl` class: the interactive loop and slash
commands. `/model` rebuilds the agent against the same sandbox/checkpointer
(cloud switch warns, local is quiet); `/models`, `/status`, `/diff` (git diff in
the sandbox), `/shell` (raw policy-gated command), `/clear` (fresh thread, files
kept), `/compact` (summarize then continue with a lean context), `/sessions`,
`/init` (write a starter `AGENTS.md`), `/help`, `/exit`.

**`telemetry.py`** — Opt-in, off by default. When enabled, records only a
per-command invocation count + last-used timestamp in `~/.openlocal/telemetry.json`.
No prompts, contents, or network. `openlocal config telemetry` displays it. The
file *is* the spec (mirrored in SECURITY.md).

---

## 4. Key data flows

**Model inference vs. code execution** — the subtlety that shapes everything:
the **LLM call happens from the host CLI process**; only the **agent's tool
calls** run inside the container. That's why `network=none` is a sane default —
the container doesn't need the network to reach the model.

**A command's journey:** model emits `execute("rm -rf build")` →
`DockerSandboxBackend.execute` → `policy.evaluate_pipeline` → `ALLOW` →
`timeout -k 5 120 sh -c 'rm -rf build'` via `docker exec` → output truncated →
`ExecuteResponse` back to the model. If it were `rm -rf /`, deny-list refuses it
before Docker is ever touched.

**A cloud turn:** user switches to `groq:…` → `build_agent` adds
`CloudRedactionMiddleware` → every model request's messages are scanned →
secrets redacted + reported → scrubbed request sent to Groq.

---

## 5. Extension points

- **New provider:** implement `Provider` + register via the `openlocal.providers`
  entry point (`pip install openlocal-provider-vllm`).
- **New sandbox:** anything implementing deepagents' `SandboxBackendProtocol`
  can replace `DockerSandboxBackend` (e.g. Podman, a cloud sandbox).
- **New subagent / house rules:** add specs in `subagents.py`, or just drop an
  `AGENTS.md` in the repo — no code change.
