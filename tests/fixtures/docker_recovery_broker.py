"""Disposable IPC broker with offline model replies for SIGKILL acceptance."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_docker_sessions_integration import OfflineToolModel, sse

from kairos_runtime.docker_backend.registry import SessionRegistry
from kairos_runtime.docker_backend.runtime import DockerSessionRuntime
from kairos_runtime.host import _attests_inactive, _Config, _Host
from kairos_runtime.service import AgentRuntimeService
from kairos_runtime.store import RuntimeStore


class RecoveryModel:
    def __init__(self, home: Path, restore: bool):
        self.home = home
        self.restore = restore
        self.completed = OfflineToolModel(restore=restore)
        self.long = OfflineToolModel()

    async def __call__(self, body):
        with (self.home / "model-calls.jsonl").open("a") as log:
            log.write(json.dumps({"restore": self.restore}) + "\n")
            log.flush()
            os.fsync(log.fileno())
        if self.restore or self.completed.calls < 2:
            async for chunk in self.completed(body):
                yield chunk
            return
        if self.long.calls:
            await asyncio.Future()
        # Keep the real exec_command pending across the broker's death. The
        # parent proves both this marker and the sleeping process exist.
        command = "printf uncommitted > /workspace/uncommitted; sleep 300"
        async for chunk in self.long(body):
            event = json.loads(chunk.decode().split("\ndata: ", 1)[1])
            if event["type"] == "response.function_call_arguments.delta":
                event["delta"] = json.dumps(
                    {"cmd": command, "workdir": "/workspace", "yield_time_ms": 1000}
                )
            item = event.get("item")
            if item and item.get("arguments"):
                item["arguments"] = json.dumps(
                    {"cmd": command, "workdir": "/workspace", "yield_time_ms": 1000}
                )
            for item in event.get("response", {}).get("output", []):
                if item.get("arguments"):
                    item["arguments"] = json.dumps(
                        {"cmd": command, "workdir": "/workspace", "yield_time_ms": 1000}
                    )
            yield sse(event)


async def serve(home: Path, project: Path, restore: bool):
    with (home / "host.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        registry = SessionRegistry(home / "sandbox-state")
        runtime = DockerSessionRuntime(
            registry,
            image="kairos:external-sandbox",
            model="gpt-5.5",
            transport=RecoveryModel(home, restore),
        )
        service = AgentRuntimeService(
            RuntimeStore(home / "state.sqlite"),
            runtime,
            allowed_directories=(str(project),),
            inactivity_confirmed=lambda generation: _attests_inactive(
                lock.fileno(), runtime, generation
            ),
        )
        stop = asyncio.Event()
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
        try:
            await runtime.recover()
            await service.recover()
            host = _Host(
                config=_Config(True, "codex", (str(project),), False, backend="docker"),
                service=service,
            )
            socket = home / "runtime.sock"
            socket.unlink(missing_ok=True)
            async with await asyncio.start_unix_server(host.handle, path=str(socket)):
                (home / "ready").write_text(runtime.generation)
                await stop.wait()
        finally:
            await service.aclose()
            registry.close()


if __name__ == "__main__":
    asyncio.run(serve(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == "restore"))
