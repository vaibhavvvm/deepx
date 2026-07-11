"""Docker backend safety tests with a fully mocked container.

The point of these tests is the invariants, not Docker itself: deny-listed
commands must NEVER reach exec, approval-declined commands must not run,
timeouts surface as truncated, and secret-shaped writes are refused.
"""

from types import SimpleNamespace

from autodev.sandbox.docker_backend import DockerSandboxBackend
from autodev.sandbox.policy import Policy


class FakeContainer:
    def __init__(self, exit_code=0, output=b"ok"):
        self.status = "running"
        self.id = "fake123"
        self._exit_code = exit_code
        self._output = output
        self.exec_calls = []
        self.put_calls = []

    def reload(self):
        pass

    def start(self):
        self.status = "running"

    def exec_run(self, cmd, **kwargs):
        self.exec_calls.append(cmd)
        return SimpleNamespace(output=self._output, exit_code=self._exit_code)

    def put_archive(self, directory, data):
        self.put_calls.append(directory)
        return True


def make_backend(container, **kw):
    b = DockerSandboxBackend(
        workdir="/tmp",
        image="python:3.12-slim",
        policy=kw.pop("policy", Policy(deny=["rm -rf /"], require_approval_for=["pip install"])),
        **kw,
    )
    b._container = container
    b._client = object()  # never used because container is preset
    return b


def test_deny_never_reaches_exec():
    c = FakeContainer()
    b = make_backend(c)
    res = b.execute("rm -rf /")
    assert res.exit_code == 126
    assert "REFUSED" in res.output
    assert c.exec_calls == []  # exec never called


def test_approval_declined_does_not_run():
    c = FakeContainer()
    b = make_backend(c, approval_callback=lambda cmd, d: False, yolo=False)
    res = b.execute("pip install requests")
    assert res.exit_code == 125
    assert "DECLINED" in res.output
    assert c.exec_calls == []


def test_approval_granted_runs():
    c = FakeContainer(exit_code=0, output=b"installed")
    b = make_backend(c, approval_callback=lambda cmd, d: True)
    res = b.execute("pip install requests")
    assert res.exit_code == 0
    assert c.exec_calls  # it ran


def test_yolo_skips_approval():
    c = FakeContainer()
    b = make_backend(c, approval_callback=lambda cmd, d: False, yolo=True)
    b.execute("pip install x")
    assert c.exec_calls  # ran despite the callback saying no


def test_timeout_marked_truncated():
    c = FakeContainer(exit_code=124, output=b"partial")
    b = make_backend(c)
    res = b.execute("sleep 999")
    assert res.exit_code == 124
    assert res.truncated
    assert "TIMED OUT" in res.output


def test_output_truncation_persists_log():
    big = b"x" * 40_000
    c = FakeContainer(exit_code=0, output=big)
    b = make_backend(c)
    res = b.execute("cat bigfile")
    assert res.truncated
    assert "truncated" in res.output


def test_protected_secret_write_refused():
    c = FakeContainer()
    b = make_backend(c)
    responses = b.upload_files([("/workspace/.ssh/id_rsa", b"KEY")])
    assert responses[0].error and "permission_denied" in responses[0].error
    assert c.put_calls == []  # never written


def test_normal_write_allowed():
    c = FakeContainer()
    b = make_backend(c)
    responses = b.upload_files([("/workspace/app.py", b"print('hi')")])
    assert responses[0].error is None
    assert c.put_calls  # written


def test_id_returns_container_id():
    c = FakeContainer()
    b = make_backend(c)
    assert b.id() == "fake123"
