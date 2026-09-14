"""Docker real, rede desligada e nenhuma credencial/modelo; opt-in explícito."""

from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from kairos_runtime.experimental.docker_worker import DockerWorker

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KAIROS_EXTERNAL_SANDBOX_TEST") != "1", reason="opt-in Docker"
    ),
]
IMAGE = "kairos:external-sandbox"


def assert_removed(worker):
    result = subprocess.run(
        [
            "docker",
            "--host",
            "unix:///var/run/docker.sock",
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{worker.name}$",
            "--format",
            "{{.Names}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == ""


@pytest.mark.skipif(
    os.environ.get("KAIROS_RUNTIME_SANDBOX_TEST") != "1", reason="opt-in host AppArmor + seccomp"
)
def test_sandbox_mode_worker_attests_runtime_profiles(tmp_path):
    """Requires the host AppArmor profile loaded and the seccomp JSON published."""
    project = tmp_path / "project"
    project.mkdir()

    async def scenario():
        worker = DockerWorker(project, image=IMAGE, sandbox=True)
        async with worker:
            assert worker.policy.sandbox is True
            assert worker.evidence["status"]["Seccomp"] == "2"
            assert worker.evidence["apparmor"] == "kairos-worker-runtime (enforce)"
            result = await worker.execute(["codex", "sandbox", "/bin/true"])
            assert result["exitCode"] == 0, result
        assert_removed(worker)

    asyncio.run(scenario())


def test_real_codex_executes_with_standard_docker_isolation(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "input.txt").write_text("project data")
    sentinel = tmp_path / "host-secret"
    sentinel.write_text("synthetic host sentinel")

    async def scenario():
        worker = DockerWorker(project, image=IMAGE, writable=True)
        async with worker:
            result = await worker.execute(["python", "-c", "print(40 + 2)"])
            assert result["exitCode"] == 0, result
            assert result["stdout"].strip() == "42"
            result = await worker.execute(
                [
                    "python",
                    "-c",
                    """
from pathlib import Path
import socket
assert Path('/workspace/input.txt').read_text() == 'project data'
Path('/workspace/output.txt').write_text('generated')
for path in ['/opt/data', '/run/secrets', '/var/run/docker.sock', HOST_SENTINEL]:
    assert not Path(path).exists(), path
try:
    Path('/etc/worker-test').write_text('bad')
except OSError:
    pass
else:
    raise AssertionError('rootfs writable')
connection = socket.socket()
connection.settimeout(1)
try:
    connection.connect(('1.1.1.1', 443))
except OSError:
    pass
else:
    raise AssertionError('network escape')
finally:
    connection.close()
print('isolation-ok')
""".replace("HOST_SENTINEL", repr(str(sentinel))),
                ]
            )
            assert result["exitCode"] == 0, result
            assert result["stdout"].strip() == "isolation-ok"
            assert worker.evidence["status"]["Seccomp"] == "2"
        assert_removed(worker)
        assert not (project / "output.txt").exists()
        assert sentinel.read_text() == "synthetic host sentinel"

    asyncio.run(scenario())


def test_readonly_cannot_be_reversed_with_chmod_or_rename(tmp_path):
    (tmp_path / "input.txt").write_text("unchanged")

    async def scenario():
        async with DockerWorker(tmp_path, image=IMAGE) as worker:
            result = await worker.execute(
                [
                    "python",
                    "-c",
                    """
from pathlib import Path
p = Path('/workspace/input.txt')
for action in [lambda: p.chmod(0o777), lambda: p.write_text('bad'),
               lambda: p.rename('/workspace/moved'),
               lambda: Path('/workspace/new').write_text('bad'),
               lambda: Path('/workspace').chmod(0o777)]:
    try:
        action()
    except OSError:
        pass
    else:
        raise AssertionError('read-only bypass')
assert p.read_text() == 'unchanged'
print('readonly-ok')
""",
                ]
            )
            assert result["exitCode"] == 0, result
            assert result["stdout"].strip() == "readonly-ok"
        assert_removed(worker)

    asyncio.run(scenario())


def test_workers_do_not_share_workspace_or_home(tmp_path):
    first = tmp_path / "one"
    second = tmp_path / "two"
    first.mkdir()
    second.mkdir()
    (first / "only-first").write_text("first worker")

    async def scenario():
        async with DockerWorker(first, image=IMAGE, writable=True) as one:
            result = await one.execute(
                [
                    "python",
                    "-c",
                    "from pathlib import Path; Path('/home/worker/private').write_text('one')",
                ]
            )
            assert result["exitCode"] == 0, result
            async with DockerWorker(second, image=IMAGE) as two:
                result = await two.execute(
                    [
                        "python",
                        "-c",
                        "from pathlib import Path; assert not Path('/workspace/only-first').exists(); "
                        "assert not Path('/home/worker/private').exists(); print('separate')",
                    ]
                )
                assert result["exitCode"] == 0, result
                assert result["stdout"].strip() == "separate"
            assert_removed(two)
        assert_removed(one)

    asyncio.run(scenario())


def test_cancellation_removes_worker_and_detached_processes(tmp_path):
    async def scenario():
        worker = DockerWorker(tmp_path, image=IMAGE, writable=True)
        async with worker:
            result = await worker.execute(
                [
                    "python",
                    "-c",
                    "import subprocess; subprocess.Popen(['sleep','300'], start_new_session=True, "
                    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)",
                ]
            )
            assert result["exitCode"] == 0, result
            running = asyncio.create_task(worker.execute(["sleep", "30"], timeout_ms=30000))
            await asyncio.sleep(0.2)
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
            assert_removed(worker)
        assert_removed(worker)

    asyncio.run(scenario())


def test_server_timeout_removes_worker_even_when_rpc_returns_normally(tmp_path):
    async def scenario():
        worker = DockerWorker(tmp_path, image=IMAGE, writable=True)
        async with worker:
            result = await worker.execute(
                [
                    "python",
                    "-I",
                    "-c",
                    "import subprocess,time; subprocess.Popen(['sleep','300'], start_new_session=True, "
                    "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
                    "time.sleep(60)",
                ],
                timeout_ms=200,
            )
            assert result["exitCode"] != 0
            assert_removed(worker)

    asyncio.run(scenario())


def test_project_cannot_shadow_kernel_attestation_modules(tmp_path):
    for module in ["json", "pathlib"]:
        (tmp_path / f"{module}.py").write_text(
            "raise RuntimeError('project module executed by probe')"
        )

    async def scenario():
        async with DockerWorker(tmp_path, image=IMAGE) as worker:
            assert worker.evidence["uid"] == 10000
            assert worker.evidence["status"]["Seccomp"] == "2"
        assert_removed(worker)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["create", "import", "initialize"])
def test_startup_cancellation_removes_real_container(tmp_path, monkeypatch, phase):
    from kairos_runtime.codex_rpc import CodexRpc

    async def scenario():
        worker = DockerWorker(tmp_path, image=IMAGE)
        reached, release = asyncio.Event(), asyncio.Event()
        original_run = worker._run
        original_call = CodexRpc.call

        async def run(*args, **kwargs):
            result = await original_run(*args, **kwargs)
            selected = (phase == "create" and args[0] == "create") or (
                phase == "import" and "tar" in args
            )
            if selected:
                reached.set()
                await release.wait()
            return result

        async def call(rpc, method, params, **kwargs):
            result = await original_call(rpc, method, params, **kwargs)
            if phase == "initialize" and method == "initialize":
                reached.set()
                await release.wait()
            return result

        monkeypatch.setattr(worker, "_run", run)
        monkeypatch.setattr(CodexRpc, "call", call)
        starting = asyncio.create_task(worker.__aenter__())
        await asyncio.wait_for(reached.wait(), timeout=15)
        starting.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        assert_removed(worker)

    asyncio.run(scenario())
