# Contributing

Thanks for helping build Auto-Dev CLI. The repo is meant to be readable in an
afternoon — keep it that way.

## Setup

```bash
git clone <repo> && cd auto-dev-cli
uv sync --extra all          # installs core + groq + llamacpp + dev deps
uv run autodev doctor
```

## Dev loop

```bash
uv run pytest                 # tests (no Docker needed — the backend is mocked)
uv run ruff check src tests   # lint
uv run ruff format src tests  # format
```

CI runs lint + type-check + unit tests, plus a slower integration job that
exercises the Docker sandbox against a mocked/tiny model.

## Project layout

See [`ARCHITECTURE.md`](./ARCHITECTURE.md) for the file-by-file map. In short:

- `providers/` — pluggable model backends (`ProviderSpec` + registry).
- `sandbox/` — Docker backend, command policy, secret scan, image select.
- `agent/` — deepagents wiring, subagents, prompts, middleware.
- `commands/` — `init` / `start` / `run` / `resume` / REPL.
- `cli.py` — Typer surface only (declarative).

## Conventions

- **Match the surrounding code** — naming, comment density, structure.
- **Safety code is load-bearing.** Changes to `sandbox/policy.py`,
  `sandbox/docker_backend.py`, or the redaction middleware need tests proving the
  invariant still holds (deny never reaches exec, approval gates, secrets
  redacted). See `tests/test_docker_backend.py` for the pattern.
- **Keep the base install light.** New heavy deps go behind an optional extra.
- **Small, focused PRs.** One concern per PR.

## Adding a provider (no core change)

Implement `autodev.providers.base.Provider` and expose a factory via the
`autodev.providers` entry point:

```toml
[project.entry-points."autodev.providers"]
vllm = "autodev_provider_vllm:make_provider"
```

## Adding a sandbox backend

Implement deepagents' `SandboxBackendProtocol` (subclass `BaseSandbox` and provide
`execute` / `id` / `upload_files` / `download_files`) and pass it to
`build_agent`. Note in your changelog if you change the backend contract — forks
of `docker_backend.py` depend on it.

## Commit / PR

- Conventional-ish subject lines (`fix:`, `feat:`, `docs:`) welcome.
- Describe the *why*, not just the *what*.
- Green tests + lint before requesting review.
