"""Opt-in real Codex tool turns with offline model SSE and durable Docker sessions.

No model credentials are loaded and no paid endpoint is called. Docker is the
only external dependency; build docker/external-sandbox/Dockerfile first.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import sys
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from kairos_runtime.client import RuntimeClient
from kairos_runtime.contracts import RuntimeSession
from kairos_runtime.docker_backend.registry import SessionRegistry
from kairos_runtime.docker_backend.runtime import DockerSessionRuntime, _SandboxAdapter
from kairos_runtime.docker_backend.worker import SessionWorker, remove_recorded_worker
from kairos_runtime.service import AgentRuntimeService
from kairos_runtime.store import RuntimeStore

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("KAIROS_EXTERNAL_SANDBOX_TEST") != "1", reason="opt-in offline Docker"
    ),
]


def sse(event: dict) -> bytes:
    return ("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n").encode()


class OfflineToolModel:
    """Emit one real exec_command call, then answer only after its output arrives."""

    def __init__(self, *, restore: bool = False):
        self.restore = restore
        self.calls = 0
        self.tool_names = []
        self.tool_output_seen = False

    async def __call__(self, body):
        self.calls += 1
        response_id = f"resp_offline_{int(self.restore)}_{self.calls}"
        self.tool_names = [tool.get("name") for tool in body.get("tools", [])]
        assert body["model"] == "gpt-5.5"
        assert body["stream"] is True
        assert self.calls <= 2, "unexpected model retry or additional tool round"
        yield sse({"type": "response.created", "response": {"id": response_id}})
        if self.calls == 1:
            assert "exec_command" in self.tool_names, self.tool_names
            command = (
                'test "$(cat /workspace/marker)" = sandbox-persisted && '
                "printf restored > /workspace/resumed && cat /workspace/marker"
                if self.restore
                else "printf sandbox-persisted > /workspace/marker && cat /workspace/marker"
            )
            item = {
                "type": "function_call",
                "id": "fc_offline",
                "call_id": "call_offline",
                "name": "exec_command",
                "arguments": json.dumps(
                    {"cmd": command, "workdir": "/workspace", "max_output_tokens": 100}
                ),
            }
            yield sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "arguments": ""},
                }
            )
            yield sse(
                {
                    "type": "response.function_call_arguments.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "delta": item["arguments"],
                }
            )
        else:
            outputs = [
                entry
                for entry in body.get("input", [])
                if entry.get("type") == "function_call_output"
                and entry.get("call_id") == "call_offline"
            ]
            assert outputs, "Codex did not execute the requested local tool"
            assert "sandbox-persisted" in json.dumps(outputs[-1]), outputs[-1]
            self.tool_output_seen = True
            item = {
                "type": "message",
                "id": "msg_offline",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "Restored checkpoint." if self.restore else "Saved checkpoint.",
                        "annotations": [],
                    }
                ],
            }
            yield sse(
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {**item, "status": "in_progress", "content": []},
                }
            )
            yield sse(
                {
                    "type": "response.output_text.delta",
                    "item_id": item["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": item["content"][0]["text"],
                }
            )
        yield sse({"type": "response.output_item.done", "output_index": 0, "item": item})
        yield sse(
            {
                "type": "response.completed",
                "response": {
                    "id": response_id,
                    "object": "response",
                    "status": "completed",
                    "output": [item],
                    "usage": {
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "total_tokens": 15,
                        "input_tokens_details": {"cached_tokens": 0},
                    },
                },
            }
        )


class ObservedRuntime(DockerSessionRuntime):
    """Keep fixture diagnostics when the service correctly hides runtime exceptions."""

    async def start_turn(self, session, turn_id, content):
        try:
            return await super().start_turn(session, turn_id, content)
        except Exception as exc:
            self.start_failure = str(exc)
            raise


def tar_text(blob: bytes, name: str) -> str:
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        stream = archive.extractfile(name)
        assert stream is not None
        return stream.read().decode()


async def wait_terminal(service, turn_id):
    async with asyncio.timeout(90):
        while True:
            turn = await service.store.get_turn(turn_id)
            if turn["state"] in {"completed", "failed", "interrupted", "cancelled"}:
                return turn
            await asyncio.sleep(0.05)


def test_real_tool_turn_checkpoint_and_broker_restart_preserve_same_thread(tmp_path):  # noqa: PLR0915 — one broker-restart acceptance story.
    async def run():  # noqa: PLR0915 — keep both generations and cleanup in one scenario.
        project = tmp_path / "project"
        project.mkdir()
        (project / "original.txt").write_text("host original")
        registry_path = tmp_path / "sandbox-state"
        store = RuntimeStore(tmp_path / "state.sqlite")
        known_workers = []

        def worker_factory(*args, **kwargs):
            worker = SessionWorker(*args, **kwargs)
            known_workers.append(worker)
            return worker

        first_model = OfflineToolModel()
        registry = SessionRegistry(registry_path)
        runtime = ObservedRuntime(
            registry,
            image="kairos:external-sandbox",
            model="gpt-5.5",
            transport=first_model,
            worker_factory=worker_factory,
        )
        service = AgentRuntimeService(
            store,
            runtime,
            allowed_directories=(str(project),),
            inactivity_confirmed=runtime.attests_inactive,
        )
        try:
            await runtime.recover()
            details = await service.create(
                str(project), "workspace_write", session_id="offline-session"
            )
            first_thread = details["external_thread_id"]
            turn_id = await service.submit(
                "offline-session", "Write the marker file with the tool.", "first"
            )
            turn = await wait_terminal(service, turn_id)
            assert turn["state"] == "completed", (
                turn["state"],
                first_model.calls,
                first_model.tool_names,
                getattr(runtime, "start_failure", None),
            )
            assert first_model.calls == 2 and first_model.tool_output_seen
            assert not registry.list_workers()
            workspace, home = registry.archives("offline-session")
            assert tar_text(workspace, "marker") == "sandbox-persisted"
            with tarfile.open(fileobj=io.BytesIO(home), mode="r:") as archive:
                assert "auth.json" not in archive.getnames()
            events = await store.events_after("offline-session", None)
            assert any(event.kind == "tool" for event in events)
            assert any(event.kind == "turn_end" for event in events)
            assert not (project / "marker").exists()
        finally:
            await service.aclose()
            registry.close()

        second_model = OfflineToolModel(restore=True)
        registry = SessionRegistry(registry_path)
        replacement = DockerSessionRuntime(
            registry,
            image="kairos:external-sandbox",
            model="gpt-5.5",
            transport=second_model,
            worker_factory=worker_factory,
        )
        service = AgentRuntimeService(
            store,
            replacement,
            allowed_directories=(str(project),),
            inactivity_confirmed=replacement.attests_inactive,
        )
        try:
            await replacement.recover()
            assert replacement.attests_inactive(runtime.generation)
            await service.recover()
            details = await service.get("offline-session")
            assert details["external_thread_id"] == first_thread
            turn_id = await service.submit(
                "offline-session", "Read the saved marker and write resumed.", "second"
            )
            turn = await wait_terminal(service, turn_id)
            assert turn["state"] == "completed", (
                turn["state"],
                second_model.calls,
                second_model.tool_names,
            )
            assert second_model.calls == 2 and second_model.tool_output_seen
            workspace, _ = registry.archives("offline-session")
            assert tar_text(workspace, "marker") == "sandbox-persisted"
            assert tar_text(workspace, "resumed") == "restored"
            assert registry.get("offline-session").external_thread_id == first_thread
            assert not registry.list_workers()
            assert not (project / "marker").exists()
            assert not (project / "resumed").exists()
            assert (project / "original.txt").read_text() == "host original"
            assert all(worker._closed for worker in known_workers)
            # Removal helper verifies actual daemon absence for every known name.
            for worker in known_workers:
                await remove_recorded_worker(worker.name, confirmed=True)
        finally:
            await service.aclose()
            registry.close()

    asyncio.run(run())


def test_real_codex_turn_uses_bridge_and_executes_local_tool(tmp_path):
    async def run():
        model = OfflineToolModel()
        async with SessionWorker(
            tmp_path,
            image="kairos:external-sandbox",
            writable=True,
            transport=model,
            model="gpt-5.5",
        ) as worker:
            adapter = _SandboxAdapter(worker)
            session = RuntimeSession("direct", "codex", "/workspace", "workspace_write")
            session = replace(session, external_thread_id=await adapter.create_thread(session))
            worker.relay.allow_turn("direct-turn")
            await adapter.start_turn(
                session, "direct-turn", "Write the marker using the command tool."
            )
            async with asyncio.timeout(60):
                events = [event async for event in adapter.observe(session, "direct-turn")]
            assert events[-1]["payload"]["state"] == "completed", (
                events,
                model.calls,
                model.tool_names,
            )
            assert model.calls == 2 and model.tool_output_seen
            await worker.relay.end_turn("direct-turn")
            workspace, _ = await worker.checkpoint()
            assert tar_text(workspace, "marker") == "sandbox-persisted"
            assert not (tmp_path / "marker").exists()

    asyncio.run(run())


async def docker_result(*args):
    process = await asyncio.create_subprocess_exec(
        "docker", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
        return process.returncode, stdout.decode(), stderr.decode()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def test_sigkill_broker_discards_uncommitted_turn_and_allows_explicit_same_thread_resume(tmp_path):  # noqa: PLR0915
    async def run():  # noqa: PLR0915 — one disposable crash/recovery lifecycle.
        project = tmp_path / "project"
        project.mkdir()
        (project / "original.txt").write_text("host original")
        home = tmp_path / "broker"
        home.mkdir(mode=0o700)
        store = RuntimeStore(home / "state.sqlite")
        registry = SessionRegistry(home / "sandbox-state")
        client = RuntimeClient(home / "runtime.sock")
        processes = []
        logs = []

        async def start(mode):
            (home / "ready").unlink(missing_ok=True)
            log = (home / f"{len(processes)}.log").open("w+")
            logs.append(log)
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(Path(__file__).parent / "fixtures" / "docker_recovery_broker.py"),
                str(home),
                str(project),
                mode,
                stdout=log,
                stderr=log,
            )
            processes.append(process)
            async with asyncio.timeout(90):
                while not (home / "ready").exists():
                    if process.returncode is not None:
                        log.seek(0)
                        pytest.fail(log.read())
                    await asyncio.sleep(0.05)
            assert (await client.status())["state"] == "ready"
            return process

        async def completed(turn_id):
            async with asyncio.timeout(90):
                while True:
                    turn = await store.get_turn(turn_id)
                    if turn["state"] in {"completed", "failed", "interrupted", "cancelled"}:
                        assert turn["state"] == "completed", turn
                        return
                    await asyncio.sleep(0.05)

        try:
            broker = await start("first")
            details = await client.create(str(project), "workspace_write", session_id="crash")
            thread = details["external_thread_id"]
            first = await client.submit("crash", "Write the marker file.", "first")
            await completed(first)
            workspace, _ = registry.archives("crash")
            baseline = registry.baseline("crash")
            assert tar_text(workspace, "marker") == "sandbox-persisted"
            old = await client.submit("crash", "Start the long tool.", "long")
            async with asyncio.timeout(60):
                while True:
                    record = registry.get("crash")
                    if record.worker_confirmed:
                        result = await docker_result(
                            "exec",
                            record.worker_name,
                            "python3",
                            "-c",
                            "from pathlib import Path; "
                            "assert Path('/workspace/uncommitted').read_text() == 'uncommitted'; "
                            "assert any(p.read_bytes() == b'sleep\\x00300\\x00' "
                            "for p in Path('/proc').glob('[0-9]*/cmdline'))",
                        )
                        if result[0] == 0:
                            break
                    await asyncio.sleep(0.05)
            old_worker = record.worker_name
            before = await store.get_turn(old)
            assert before["state"] == "running" and before["external_turn_id"]
            journal = await store.events_after("crash", None)
            assert any(event.kind == "tool" and event.turn_id == old for event in journal)
            broker.send_signal(signal.SIGKILL)
            assert await asyncio.wait_for(broker.wait(), 10) == -signal.SIGKILL
            calls = (home / "model-calls.jsonl").read_text()
            assert len(calls.splitlines()) >= 3
            assert (await docker_result("inspect", old_worker))[0] == 0
            await start("restore")
            interrupted = await store.get_turn(old)
            assert interrupted["state"] == "interrupted"
            events = await store.events_after("crash", None)
            ends = [e for e in events if e.turn_id == old and e.kind == "turn_end"]
            assert len(ends) == 1 and ends[0].payload["state"] == "interrupted"
            assert events[: len(journal)] == journal
            assert (home / "model-calls.jsonl").read_text() == calls
            assert (await docker_result("inspect", old_worker))[0] != 0
            assert registry.archives("crash")[0] == workspace
            assert registry.baseline("crash") == baseline
            assert not registry.list_workers()
            assert (await client.get("crash"))["external_thread_id"] == thread
            assert interrupted["inactive_confirmed_at"] is not None
            resumed = await client.submit("crash", "Read marker and write resumed.", "resume")
            await completed(resumed)
            restored, _ = registry.archives("crash")
            assert tar_text(restored, "marker") == "sandbox-persisted"
            assert tar_text(restored, "resumed") == "restored"
            with tarfile.open(fileobj=io.BytesIO(restored), mode="r:") as archive:
                assert "uncommitted" not in archive.getnames()
            assert registry.get("crash").external_thread_id == thread
            assert registry.baseline("crash") == baseline
            assert not registry.list_workers()
            assert (project / "original.txt").read_text() == "host original"
            assert sorted(p.name for p in project.iterdir()) == ["original.txt"]
        finally:
            await client.aclose()
            for process in processes:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 15)
                    except TimeoutError:
                        process.kill()
                        await process.wait()
            for log in logs:
                log.close()
            try:
                for record in registry.list_workers():
                    await remove_recorded_worker(
                        record.worker_name, confirmed=record.worker_confirmed
                    )
            finally:
                registry.close()

    # Includes IPC calls as well as the individually bounded readiness polls.
    asyncio.run(asyncio.wait_for(run(), 300))
