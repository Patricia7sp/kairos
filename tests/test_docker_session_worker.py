"""Session worker lifecycle, bounded exports and real offline checkpoint recovery."""

import asyncio
import io
import os
import sys
import tarfile

import pytest

from kairos_runtime.docker_backend.worker import SessionWorker
from kairos_runtime.experimental.snapshot import snapshot_project


async def no_transport(_body):
    raise AssertionError("no model call expected")
    yield b""  # pragma: no cover


def empty_archive():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w"):
        pass
    return output.getvalue()


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kairos_runtime.experimental.docker_worker.shutil.which", lambda _: "/usr/bin/docker"
    )
    return SessionWorker(
        tmp_path,
        image="kairos:external-sandbox",
        writable=True,
        transport=no_transport,
        model="gpt-5.5",
    )


def test_checkpoint_snapshot_keeps_generated_hidden_state(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("worker git")
    (tmp_path / ".env.local").write_text("synthetic worker variable")
    with tarfile.open(fileobj=io.BytesIO(snapshot_project(tmp_path, include_all=True))) as tar:
        assert tar.getnames() == [".env.local", ".git", ".git/config"]
    with tarfile.open(fileobj=io.BytesIO(snapshot_project(tmp_path))) as tar:
        assert tar.getnames() == []


def test_invalid_restored_archive_rejected_before_docker(tmp_path, monkeypatch):
    monkeypatch.setattr("kairos_runtime.experimental.docker_worker.shutil.which", lambda _: None)
    with pytest.raises(ValueError):
        SessionWorker(
            tmp_path,
            image="test",
            transport=no_transport,
            model="gpt-5.5",
            workspace_archive=b"bad",
        )


def test_start_restores_home_before_credential_free_server(worker, monkeypatch):
    async def scenario():
        events = []
        worker._workspace_archive = empty_archive()
        worker._home_archive = empty_archive()
        bridge_reader = asyncio.StreamReader()
        bridge_reader.feed_data(b'{"type":"ready","protocol":1}\n')

        class Writer:
            def close(self):
                pass

            async def wait_closed(self):
                pass

        class Bridge:
            stdout = bridge_reader
            stdin = Writer()
            stderr = asyncio.StreamReader()

        async def prepare(blob):
            events.append(("prepare", blob))

        async def run(*args, **kwargs):
            events.append(("restore", args, kwargs))

        async def spawn(*args):
            events.append(("bridge", args))
            return Bridge()

        async def server(args):
            events.append(("server", args))

        monkeypatch.setattr(worker, "_prepare", prepare)
        monkeypatch.setattr(worker, "_run", run)
        monkeypatch.setattr(worker, "_spawn", spawn)
        monkeypatch.setattr(worker, "_start_server", server)
        await worker._start()
        assert [event[0] for event in events] == ["prepare", "restore", "bridge", "server"]
        assert events[1][1][-1] == "/home/worker/.codex"
        assert events[1][2]["input_data"] == empty_archive()
        assert events[2][1][-4:] == (
            "/usr/local/bin/python",
            "-I",
            "-u",
            "/usr/local/lib/kairos-worker/model_bridge.py",
        )
        args = " ".join(events[3][1])
        assert 'model_provider="kairos"' in args
        assert "http://127.0.0.1:8765" in args
        assert "requires_openai_auth=false" in args
        assert "supports_websockets=false" in args
        assert 'web_search="disabled"' in args
        worker.relay.allow_turn("turn-1")
        await worker.relay.end_turn("turn-1")
        await worker.relay.aclose()
        worker._bridge_stderr.cancel()
        await asyncio.gather(worker._bridge_stderr, return_exceptions=True)
        worker._config.cleanup()

    asyncio.run(scenario())


def test_checkpoint_waits_for_unmodified_remote_exec_exit(worker, monkeypatch):
    async def scenario():
        events = []
        release, waiting = asyncio.Event(), asyncio.Event()

        class Rpc:
            async def aclose(self):
                events.append("rpc-closed")

        class Process:
            stdout = asyncio.StreamReader()
            returncode = None

            async def wait(self):
                waiting.set()
                await release.wait()
                self.returncode = 0
                self.stdout.feed_eof()
                events.append("remote-exited")
                return 0

        async def export(path):
            events.append(path)
            return empty_archive()

        async def quiesce():
            events.append("writers-frozen")

        worker._rpc, worker._process, worker._owned = Rpc(), Process(), True
        monkeypatch.setattr(worker, "_export", export)
        monkeypatch.setattr(worker, "_quiesce", quiesce)
        task = asyncio.create_task(worker.checkpoint())
        await waiting.wait()
        assert events == ["rpc-closed"]
        assert not task.done()
        release.set()
        assert await task == (empty_archive(), empty_archive())
        assert events == [
            "rpc-closed",
            "remote-exited",
            "writers-frozen",
            "/workspace",
            "/home/worker/.codex",
        ]
        worker._owned = False
        await worker.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_uncertain_remote_exit_destroys_worker_without_export(worker, monkeypatch, failure):
    async def scenario():
        import kairos_runtime.docker_backend.worker as module

        events = []

        class Rpc:
            async def aclose(self):
                pass

        class Process:
            stdout = asyncio.StreamReader()
            returncode = None

            async def wait(self):
                if failure == "timeout":
                    await asyncio.Event().wait()
                self.returncode = 1
                self.stdout.feed_eof()
                return 1

        async def cleanup():
            events.append("removed")
            worker._owned = False
            worker._config.cleanup()

        async def export(_path):
            raise AssertionError("must not export an uncertain live server")

        monkeypatch.setattr(module, "SHUTDOWN_TIMEOUT", 0.02)
        monkeypatch.setattr(worker, "aclose", cleanup)
        monkeypatch.setattr(worker, "_export", export)
        worker._rpc, worker._process, worker._owned = Rpc(), Process(), True
        with pytest.raises((RuntimeError, TimeoutError)):
            await worker.checkpoint()
        assert events == ["removed"]

    asyncio.run(scenario())


def test_export_bounds_real_subprocess_stdout_and_reaps(worker, monkeypatch):
    async def scenario():
        import kairos_runtime.docker_backend.worker as module

        processes = []

        async def spawn(*_args):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import sys,time; sys.stdout.buffer.write(b'x'*100000); sys.stdout.flush(); time.sleep(60)",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(process)
            return process

        monkeypatch.setattr(module, "MAX_ARCHIVE_BYTES", 1024)
        monkeypatch.setattr(worker, "_spawn", spawn)
        with pytest.raises(ValueError):
            await worker._export("/workspace")
        assert processes[0].returncode is not None
        await worker.aclose()

    asyncio.run(scenario())


def test_recovery_removes_exact_recorded_name_without_starting(monkeypatch):
    from kairos_runtime.docker_backend.worker import remove_recorded_worker

    async def scenario():
        removed = []

        async def remove(worker):
            removed.append(worker.name)
            worker._owned = False

        monkeypatch.setattr(
            "kairos_runtime.experimental.docker_worker.shutil.which", lambda _: "/usr/bin/docker"
        )
        monkeypatch.setattr(
            "kairos_runtime.experimental.docker_worker.DockerWorker._remove", remove
        )
        name = "kairos-worker-" + "a" * 32
        await remove_recorded_worker(name)
        assert removed == [name]
        with pytest.raises(ValueError):
            await remove_recorded_worker("--all")

    asyncio.run(scenario())


@pytest.mark.parametrize("confirmed,present", [(False, False), (True, False), (False, True)])
def test_recovery_preserves_uncertain_creation_until_container_is_observed(
    monkeypatch, confirmed, present
):
    from kairos_runtime.docker_backend.worker import remove_recorded_worker

    async def scenario():
        operations = []

        async def daemon(_worker, *args, **_kwargs):
            operations.append(args[0])
            if args[0] == "inspect":
                if not present:
                    return 1, b"", b""
                return 0, b'[{"Config":{"Labels":{"io.kairos.external-sandbox":"prototype"}}}]', b""
            return 0, b"", b""

        monkeypatch.setattr(
            "kairos_runtime.experimental.docker_worker.shutil.which", lambda _: "/usr/bin/docker"
        )
        monkeypatch.setattr("kairos_runtime.experimental.docker_worker.DockerWorker._run", daemon)
        name = "kairos-worker-" + "b" * 32
        if not confirmed and not present:
            with pytest.raises(RuntimeError, match="ownership retido"):
                await remove_recorded_worker(name, confirmed=confirmed)
            assert operations == ["inspect"]
        else:
            await remove_recorded_worker(name, confirmed=confirmed)
            assert operations == (
                ["inspect", "rm", "container"] if present else ["inspect", "container"]
            )

    asyncio.run(scenario())


@pytest.mark.integration
@pytest.mark.skipif(os.environ.get("KAIROS_EXTERNAL_SANDBOX_TEST") != "1", reason="opt-in Docker")
def test_real_session_checkpoint_and_restore_without_credentials(tmp_path):
    async def scenario():
        (tmp_path / "original").write_text("source")
        first = SessionWorker(
            tmp_path,
            image="kairos:external-sandbox",
            writable=True,
            transport=no_transport,
            model="gpt-5.5",
        )
        async with first:
            result = await first.execute(
                [
                    "python",
                    "-I",
                    "-c",
                    "from pathlib import Path; Path('/workspace/generated').write_text('checkpoint'); Path('/workspace/.env.worker').write_text('worker data'); Path('/home/worker/.codex/private').write_text('session data')",
                ]
            )
            assert result["exitCode"] == 0
            result = await first.execute(
                [
                    "python",
                    "-I",
                    "-c",
                    "import subprocess; subprocess.Popen(['python','-I','-c',\"import time; from pathlib import Path; p=Path('/workspace/counter'); i=0\\nwhile True:\\n i+=1; p.write_text(str(i)); time.sleep(.01)\"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)",
                ]
            )
            assert result["exitCode"] == 0
            await asyncio.sleep(0.1)
            workspace, home = await first.checkpoint()
            with tarfile.open(fileobj=io.BytesIO(workspace)) as tar:
                counter = tar.extractfile("counter").read()
            await asyncio.sleep(0.1)
            _, current_counter, _ = await first._run(
                "exec", first.name, "cat", "/workspace/counter"
            )
            assert current_counter == counter
        assert not (tmp_path / "generated").exists()
        with tarfile.open(fileobj=io.BytesIO(home)) as tar:
            assert "auth.json" not in tar.getnames()
        async with SessionWorker(
            tmp_path,
            image="kairos:external-sandbox",
            writable=True,
            transport=no_transport,
            model="gpt-5.5",
            workspace_archive=workspace,
            home_archive=home,
        ) as second:
            result = await second.execute(
                [
                    "python",
                    "-I",
                    "-c",
                    "from pathlib import Path; assert Path('/workspace/generated').read_text() == 'checkpoint'; assert Path('/workspace/.env.worker').read_text() == 'worker data'; assert Path('/home/worker/.codex/private').read_text() == 'session data'; print('restored')",
                ]
            )
            assert result["stdout"].strip() == "restored"

    asyncio.run(scenario())
