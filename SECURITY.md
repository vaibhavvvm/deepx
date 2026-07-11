# Security & Threat Model

Auto-Dev CLI runs a language model that can propose and execute shell commands
and file edits. This document states exactly what is and isn't protected, so you
can decide whether that's acceptable for your repo — no hand-waving.

## What runs where

The subtlety everything else follows from:

- **Model inference runs on the host** (the CLI process), or on Groq's servers
  for cloud models. It does **not** run inside the container.
- **The agent's tool calls** (shell commands, file reads/writes) run **inside a
  Docker container** bind-mounted to your repo at `/workspace`.

So the container isolates *code execution*, not *model inference*. That's why
`network = none` is a safe default: the model doesn't need the container's
network to think.

## Controls

### 1. Deny-list — absolute
Commands matching `[policy] deny` (disk formatters, fork bombs, `dd if=`, …) are
refused **inside the sandbox backend, before Docker is ever called**. There is no
override — not even `--yolo`. Deny is also evaluated per-stage across pipes
(`a | b | c`) and against a whitespace-normalized form, so obfuscated spacing
can't dodge it.

### 2. Approval gate — the default safety net
Commands matching `[policy] require_approval_for` (git push, package publish,
`rm -rf`, `pip install`, outbound `curl`/`wget`, …) pause for an interactive
`y / n / a` (`a` = allow this rule for the session). Bypassable **only** by the
explicit `--yolo` flag (or `--yes` in non-interactive `run`). This is the single
most important control; keep the list comprehensive for your workflow.

### 3. Container hardening
Every sandbox container runs:
- **Unprivileged** — mapped to your host UID/GID on Linux (correct file
  ownership; no root in the container).
- `--cap-drop ALL` and `--security-opt no-new-privileges`.
- **Resource-limited** — CPU (`nano_cpus`), memory (`mem_limit`), and process
  count (`pids_limit` + `nproc` ulimit) caps to stop a runaway fork bomb.
- **Hard command timeout** — a blocking command (e.g. a dev server) is killed
  with its process tree after `timeout_seconds` and returns partial output.

### 4. Network policy
- `none` (default): no container network at all.
- `restricted`: bridge network with `host.docker.internal` mapped to the host
  gateway so a host-local Ollama/llama.cpp is reachable. **Limitation:** this is
  *not* a hard egress firewall — broad outbound traffic is still possible. If you
  need guaranteed egress control, keep `none` or run the daemon behind your own
  firewall rules.
- `full`: normal networking for `pip install`, `npm install`, etc. Deny-list
  still enforced.

### 5. Cloud data flow — visible and scrubbed
- The active model and its posture are always shown: `[local]` or
  `[cloud: groq]`, plus the network policy.
- Switching **into** a cloud model prints a one-line "data leaves your machine"
  warning. Switching **back** to local is silent.
- For cloud models only, a `wrap_model_call` middleware scans every outgoing
  request and **redacts secret-shaped strings** — known key formats (AWS, Groq,
  OpenAI, GitHub, Google, Slack), PEM private-key blocks, `KEY=/TOKEN=/SECRET=`
  assignments, bearer tokens, and high-entropy opaque tokens. Redactions are
  reported with non-reversible previews. For local models this middleware is not
  installed at all — nothing leaves the machine, so there is nothing to scan.

### 6. Secret-file write guard
The agent is refused writes to secret-shaped paths (`id_rsa`, `*.pem`, `~/.ssh/`,
`~/.aws/credentials`, `.netrc`, `*.p12/pfx`) so a hijacked agent can't clobber or
plant credentials. Toggle with `[sandbox] protect_secret_files`.

### 7. Prompt injection from repo content
A malicious README or code comment may try to instruct the agent to exfiltrate
secrets or run dangerous commands. Mitigations:
- File content read via `read_file` and the project `AGENTS.md` are framed as
  **data**, not instructions, in the system prompt.
- The **approval gate on `execute` is the real backstop** — regardless of what
  convinces the model to try something, a human still confirms risky commands and
  the deny-list still refuses destructive ones.

Treat prompt injection as a live risk on untrusted repos: prefer `network=none`,
keep the approval list broad, and don't use `--yolo` on code you don't trust.

## Telemetry

**Off by default.** When enabled (`[telemetry] enabled = true`), exactly one
thing is collected, locally, in `~/.autodev/telemetry.json`: a per-command
invocation count and last-used timestamp. No prompts, no file contents, no model
output, no network, no auto-upload. View it with `autodev config telemetry`. The
code in `src/autodev/telemetry.py` is the complete specification.

## Reporting a vulnerability

Please report security issues privately to the maintainers (open a GitHub
security advisory or email the maintainer address in `pyproject.toml`) rather
than a public issue. We aim to acknowledge within a few days.

## Known limitations

- `restricted` network is not a hard egress firewall (see §4).
- Docker is the trust boundary; a Docker daemon compromise or a kernel-level
  container escape is out of scope.
- The secret scanner is heuristic — it reduces, not eliminates, the chance of a
  secret reaching a cloud provider. Don't rely on it as your only control for
  highly sensitive repos; prefer local models there.
