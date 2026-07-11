"""Docker-backed sandbox implementing deepagents' ``SandboxBackendProtocol``.

We subclass :class:`deepagents.backends.sandbox.BaseSandbox`, which derives all
of the filesystem operations (``ls``/``read``/``write``/``edit``/``grep``) from
four primitives we provide:

* :meth:`execute`        -- run a shell command via ``docker exec``
* :meth:`id`             -- the container id
* :meth:`upload_files`   -- stream bytes into the container (``put_archive``)
* :meth:`download_files` -- stream bytes out (``get_archive``)

Two safety guarantees are enforced *here*, in the backend, so they hold no
matter what convinces the model to try something:

1. **Deny-list is absolute.** A denied command never reaches ``docker exec`` --
   not even under ``--yolo``.
2. **Approval gate.** Approval-listed commands call ``approval_callback`` before
   running; a ``False`` return refuses the command.

The container itself runs unprivileged, with CPU/memory/PID limits and a
configurable network policy. Model *inference* never happens inside the
container -- only the agent's tool calls do (blueprint 7.3).
"""

from __future__ import annotations

import io
import os
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

from autodev.sandbox.policy import Policy, PolicyDecision

WORKSPACE = "/workspace"
_MAX_OUTPUT_BYTES = 16_000  # token-safe cap before returning to the model
_LOG_DIR = f"{WORKSPACE}/.autodev/logs"

# approval_callback(command, PolicyDecision) -> bool  (True == allow)
ApprovalCallback = Callable[[str, PolicyDecision], bool]

# Files the agent must never write over (private keys, credential stores). The
# agent can still *read* these unless excluded, but writes are refused so a
# hijacked agent can't clobber or plant credentials.
_PROTECTED_SECRET_PATTERNS = (
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    ".pem",
    ".ppk",
    "/.ssh/",
    "/.aws/credentials",
    "/.netrc",
    ".p12",
    ".pfx",
)


class SandboxUnavailable(RuntimeError):
    """Raised when the Docker sandbox cannot be created or reached."""


def _is_protected_secret(path: str) -> bool:
    p = path.lower()
    return any(pat in p for pat in _PROTECTED_SECRET_PATTERNS)


@dataclass
class SandboxLimits:
    cpu_limit: str = "2"
    memory_limit: str = "4g"
    pids_limit: int = 512
    timeout_seconds: int = 120


class DockerSandboxBackend(BaseSandbox):
    def __init__(
        self,
        workdir: str | Path,
        image: str,
        *,
        network: str = "none",
        limits: SandboxLimits | None = None,
        policy: Policy | None = None,
        approval_callback: ApprovalCallback | None = None,
        yolo: bool = False,
        container_name: str | None = None,
        keep_alive: bool = False,
        session_id: str | None = None,
        protect_secret_files: bool = True,
    ):
        self.workdir = str(Path(workdir).resolve())
        self.image = image
        self.network = network
        self.limits = limits or SandboxLimits()
        self.policy = policy or Policy()
        self.approval_callback = approval_callback
        self.yolo = yolo
        self.keep_alive = keep_alive
        self.session_id = session_id
        self.protect_secret_files = protect_secret_files
        self._container_name = container_name
        self._client = None
        self._container = None

    # -- lifecycle -------------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            import docker

            try:
                self._client = docker.from_env()
                self._client.ping()
            except Exception as exc:
                raise SandboxUnavailable(
                    "Docker daemon is not reachable. Is Docker running? "
                    "Run 'autodev doctor' for details.\n"
                    f"underlying error: {exc}"
                ) from exc
        return self._client

    def start(self) -> None:
        """Create (or reattach to) the container. Idempotent."""
        if self._container is not None:
            return
        from docker.types import Ulimit

        import docker

        # Reattach to a named keep-alive container if one already exists.
        if self._container_name:
            try:
                existing = self.client.containers.get(self._container_name)
                if existing.status != "running":
                    existing.start()
                self._container = existing
                self._ensure_log_dir()
                return
            except docker.errors.NotFound:
                pass

        self._ensure_image()

        network_mode, extra_hosts, network_disabled = _network_settings(self.network)

        # Run unprivileged, mapping to the host UID/GID on Linux so files written
        # by the container are owned correctly on the host.
        user = None
        if os.name == "posix" and hasattr(os, "getuid"):
            user = f"{os.getuid()}:{os.getgid()}"

        self._container = self.client.containers.run(
            self.image,
            command=["sleep", "infinity"],
            name=self._container_name,
            detach=True,
            working_dir=WORKSPACE,
            volumes={self.workdir: {"bind": WORKSPACE, "mode": "rw"}},
            user=user,
            environment={"HOME": WORKSPACE, "AUTODEV_SESSION": self.session_id or ""},
            network_mode=network_mode,
            network_disabled=network_disabled,
            extra_hosts=extra_hosts or None,
            mem_limit=self.limits.memory_limit,
            nano_cpus=int(float(self.limits.cpu_limit) * 1_000_000_000),
            pids_limit=self.limits.pids_limit,
            security_opt=["no-new-privileges"],
            cap_drop=["ALL"],
            ulimits=[
                Ulimit(name="nproc", soft=self.limits.pids_limit, hard=self.limits.pids_limit)
            ],
            labels={"autodev.session": self.session_id or "", "autodev": "1"},
            auto_remove=False,
        )
        self._ensure_log_dir()

    def _ensure_image(self) -> None:
        import docker

        try:
            self.client.images.get(self.image)
        except docker.errors.ImageNotFound:
            try:
                self.client.images.pull(self.image)
            except Exception as exc:
                raise SandboxUnavailable(
                    f"Could not pull sandbox image '{self.image}': {exc}. "
                    "Check network access or pin a locally-available image in "
                    ".autodev.toml ([sandbox] image = ...)."
                ) from exc

    def _ensure_running(self) -> None:
        """Guarantee a live container before an exec/upload/download.

        Survives a container that crashed, was killed, or (for keep-alive)
        exited between turns -- reload status and restart, recreating from
        scratch if it vanished entirely.
        """
        if self._container is None:
            self.start()
            return
        try:
            self._container.reload()
            status = self._container.status
            if status in ("exited", "created", "paused"):
                self._container.start()
                self._container.reload()
            elif status == "dead":
                raise RuntimeError("container is dead")
        except Exception:
            # Container is gone or unrecoverable -- recreate.
            self._container = None
            self.start()

    def _ensure_log_dir(self) -> None:
        # Best-effort; the workspace is bind-mounted so this persists to host.
        self._raw_exec(f"mkdir -p {_LOG_DIR}")

    def stop(self) -> None:
        """Stop and (unless keep-alive) remove the container."""
        if self._container is None:
            return
        try:
            if self.keep_alive:
                self._container.stop()
            else:
                self._container.remove(force=True)
        except Exception:  # pragma: no cover - teardown must not raise
            pass
        finally:
            self._container = None

    def __enter__(self) -> DockerSandboxBackend:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # -- protocol primitives ---------------------------------------------------
    def id(self) -> str:
        if self._container is None:
            return self._container_name or "uninitialized"
        return self._container.id

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run ``command`` in the container after policy checks.

        Deny -> refuse without touching Docker. Approve -> confirm first.
        """
        decision = self.policy.evaluate_pipeline(command)

        if decision.is_denied:
            return ExecuteResponse(
                output=f"REFUSED: {decision.reason}",
                exit_code=126,
                truncated=False,
            )

        if decision.needs_approval and not self.yolo:
            approved = True
            if self.approval_callback is not None:
                approved = self.approval_callback(command, decision)
            if not approved:
                return ExecuteResponse(
                    output=(
                        f"DECLINED by user: command matched approval rule "
                        f"'{decision.matched_rule}'."
                    ),
                    exit_code=125,
                    truncated=False,
                )

        self._ensure_running()

        eff_timeout = timeout or self.limits.timeout_seconds
        return self._run_with_timeout(command, eff_timeout)

    def _run_with_timeout(self, command: str, timeout: int) -> ExecuteResponse:
        """Execute with a hard timeout using coreutils ``timeout``.

        Exit code 124 => the command exceeded the wall clock and its process
        tree was killed; we surface partial output with ``truncated=True``.
        """
        wrapped = f"timeout -k 5 {int(timeout)} /bin/sh -c {_shq(command)}"
        result = self._raw_exec(wrapped)
        output = result["output"]
        exit_code = result["exit_code"]

        timed_out = exit_code == 124
        full_output = output
        truncated = timed_out

        if len(output.encode("utf-8", "replace")) > _MAX_OUTPUT_BYTES:
            truncated = True
            log_path = self._persist_log(full_output)
            output = _tail_bytes(output, _MAX_OUTPUT_BYTES)
            output += (
                f"\n\n[output truncated to last {_MAX_OUTPUT_BYTES} bytes; "
                f"full log at {log_path} -- use read_file to see more]"
            )
        if timed_out:
            output += f"\n\n[TIMED OUT after {timeout}s; process tree killed]"

        return ExecuteResponse(output=output, exit_code=exit_code, truncated=truncated)

    def _raw_exec(self, command: str) -> dict:
        """Low-level ``docker exec`` returning combined output + exit code.

        Bypasses policy; used only for internal plumbing (mkdir, timeout wrap).
        Retries once through a container restart if the daemon reports the exec
        target went away between turns.
        """
        import docker

        self._ensure_running()
        for attempt in (1, 2):
            try:
                res = self._container.exec_run(
                    cmd=["/bin/sh", "-c", command],
                    workdir=WORKSPACE,
                    demux=False,
                    environment={"HOME": WORKSPACE},
                )
                out = res.output
                if isinstance(out, bytes):
                    out = out.decode("utf-8", "replace")
                return {"output": out or "", "exit_code": res.exit_code}
            except (docker.errors.APIError, docker.errors.NotFound):
                if attempt == 2:
                    raise
                self._container = None
                self._ensure_running()
        raise RuntimeError("unreachable")

    def _persist_log(self, content: str) -> str:
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = f"{_LOG_DIR}/exec-{ts}.log"
        self.upload_files([(path, content.encode("utf-8", "replace"))])
        return path

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        self._ensure_running()
        responses: list[FileUploadResponse] = []
        for path, data in files:
            if self.protect_secret_files and _is_protected_secret(path):
                responses.append(
                    FileUploadResponse(
                        path=path,
                        error=(
                            "permission_denied: refusing to write a secret-shaped "
                            f"file '{path}'. Set sandbox.protect_secret_files=false "
                            "to override."
                        ),
                    )
                )
                continue
            try:
                self._put_file(path, data)
                responses.append(FileUploadResponse(path=path, error=None))
            except Exception as exc:  # pragma: no cover - docker edge cases
                responses.append(FileUploadResponse(path=path, error=str(exc)))
        return responses

    def _put_file(self, path: str, data: bytes) -> None:
        directory = os.path.dirname(path) or "/"
        name = os.path.basename(path)
        # Ensure parent dir exists.
        self._raw_exec(f"mkdir -p {_shq(directory)}")

        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = int(time.time())
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(data))
        stream.seek(0)
        ok = self._container.put_archive(directory, stream.getvalue())
        if not ok:
            raise RuntimeError(f"put_archive failed for {path}")

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        self._ensure_running()
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                data = self._get_file(path)
                responses.append(FileDownloadResponse(path=path, content=data, error=None))
            except KeyError:
                responses.append(
                    FileDownloadResponse(path=path, content=None, error="file_not_found")
                )
            except Exception as exc:  # pragma: no cover - docker edge cases
                responses.append(FileDownloadResponse(path=path, content=None, error=str(exc)))
        return responses

    def _get_file(self, path: str) -> bytes:
        bits, _stat = self._container.get_archive(path)
        buf = io.BytesIO()
        for chunk in bits:
            buf.write(chunk)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            if extracted is None:
                raise KeyError(path)
            return extracted.read()


def _network_settings(network: str) -> tuple[str | None, dict, bool]:
    """Translate a network policy into Docker run kwargs.

    * none       -> no network at all (safest default). Inference runs on the
                    host, not in the container, so this rarely limits real work.
    * restricted -> bridge network with host-gateway mapping so a host-local
                    Ollama/llama.cpp is reachable; broad egress is *not* firewalled
                    (a genuine limitation, documented in SECURITY.md).
    * full       -> normal bridge networking for ``pip install`` et al.
    """
    if network == "none":
        return "none", {}, True
    if network == "restricted":
        return "bridge", {"host.docker.internal": "host-gateway"}, False
    if network == "full":
        return "bridge", {"host.docker.internal": "host-gateway"}, False
    raise ValueError(f"Unknown network policy '{network}' (none|restricted|full)")


def _shq(s: str) -> str:
    """Single-quote a string for safe embedding in ``sh -c``."""
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _tail_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", "replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", "replace")
