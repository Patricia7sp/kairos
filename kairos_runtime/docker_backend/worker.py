"""Offline per-session worker with a broker-only model relay and opaque exports."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from kairos_providers._async_cleanup import run_persistent_cleanup

from ..codex_rpc import CodexRpc
from ..experimental.docker_worker import DockerWorker
from ..experimental.snapshot import snapshot_project
from ..experimental.worker_policy import ENVIRONMENT
from ..supervisor import CodexSupervisor
from .archives import MAX_ARCHIVE_BYTES, validate_archive
from .model_relay import BRIDGE_HOST, BRIDGE_PORT, ModelRelay, Transport

SHUTDOWN_TIMEOUT = 15.0
EXPORT_TIMEOUT = 30.0
_TRUSTED_ROOT = "/usr/local/lib/kairos-worker"
_EXPORT = """
import runpy, sys
from pathlib import Path
snapshot = runpy.run_path('/usr/local/lib/kairos-worker/snapshot.py')['snapshot_project']
sys.stdout.buffer.write(snapshot(Path(sys.argv[1]), include_all=True))
"""
_QUIESCE = """
import os, signal, time
from pathlib import Path
own_pid = os.getpid()
for attempt in range(100):
    pending = False
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name) in (1, own_pid):
            continue
        try:
            status = Path('/proc', name, 'status').read_text()
            state = next(line for line in status.splitlines() if line.startswith('State:')).split()[1]
            if state not in ('T', 't', 'Z', 'X'):
                pending = True
                os.kill(int(name), signal.SIGSTOP)
        except (FileNotFoundError, ProcessLookupError):
            pass
    if not pending:
        break
    time.sleep(.01)
else:
    raise RuntimeError('worker processes did not quiesce')
"""


class SessionWorker(DockerWorker):
    def __init__(
        self,
        project: Path,
        *,
        image: str,
        transport: Transport,
        model: str,
        writable: bool = False,
        workspace_archive: bytes | None = None,
        home_archive: bytes | None = None,
    ):
        if not isinstance(model, str) or not model or "\x00" in model:
            raise ValueError("invalid worker model")
        for archive in (workspace_archive, home_archive):
            if archive is not None:
                validate_archive(archive)
        super().__init__(project, image=image, writable=writable)
        self._workspace_archive = workspace_archive
        self._home_archive = home_archive
        self._transport = transport
        self._model = model
        self._bridge: asyncio.subprocess.Process | None = None
        self._bridge_stderr: asyncio.Task | None = None
        self._relay: ModelRelay | None = None
        self._checkpoint_lock = asyncio.Lock()
        self._checkpoint_started = False

    @property
    def rpc(self) -> CodexRpc:
        if self._rpc is None or self._closing or self._closed:
            raise RuntimeError("worker RPC unavailable")
        return self._rpc

    @property
    def generation(self) -> str:
        return self.rpc.generation

    @property
    def relay(self) -> ModelRelay:
        if self._relay is None:
            raise RuntimeError("worker relay unavailable")
        return self._relay

    def _server_args(self) -> list[str]:
        # Pin the entire provider table, including empty header maps. A restored
        # worker config cannot substitute host credentials or an upstream route.
        provider = (
            '{name="Kairos broker",base_url="http://'
            + BRIDGE_HOST
            + ":"
            + str(BRIDGE_PORT)
            + '",wire_api="responses",requires_openai_auth=false,supports_websockets=false,'
            "http_headers={},env_http_headers={},request_max_retries=0,stream_max_retries=0}"
        )
        config = [
            'model_provider="kairos"',
            f"model={json.dumps(self._model)}",
            f"model_providers.kairos={provider}",
            'web_search="disabled"',
            "features.image_generation=false",
            "features.image_generation_tool=false",
            "features.responses_websockets=false",
            "features.responses_websockets_v2=false",
            "features.remote_compaction=false",
        ]
        return [part for setting in config for part in ("-c", setting)]

    async def _start(self):
        archive = self._workspace_archive
        if archive is None:
            archive = snapshot_project(self.project)
        await self._prepare(validate_archive(archive))
        if self._home_archive is not None:
            await self._run(
                "exec",
                "-i",
                self.name,
                "tar",
                "--extract",
                "--no-same-owner",
                "--no-same-permissions",
                "-C",
                ENVIRONMENT["CODEX_HOME"],
                input_data=self._home_archive,
            )
        self._bridge = await self._spawn(
            "exec",
            "-i",
            self.name,
            "/usr/local/bin/python",
            "-I",
            "-u",
            f"{_TRUSTED_ROOT}/model_bridge.py",
        )
        assert self._bridge.stdout is not None and self._bridge.stdin is not None
        assert self._bridge.stderr is not None
        self._bridge_stderr = asyncio.create_task(self._discard_stderr(self._bridge.stderr))
        self._relay = ModelRelay(
            self._bridge.stdout, self._bridge.stdin, self._transport, self._model
        )
        await self._relay.start()
        await self._start_server(self._server_args())

    async def _stop_server(self):
        if self._rpc is None or self._process is None:
            raise RuntimeError("worker server is not active")
        process = self._process
        # Do NOT signal/terminate the docker client here: only its untouched,
        # successful exit proves that the daemon-observed remote exec completed.
        async with asyncio.timeout(SHUTDOWN_TIMEOUT):
            await self._rpc.aclose()
            self._rpc = None
            drain = asyncio.create_task(self._discard_stderr(process.stdout))
            try:
                code = await process.wait()
                await drain
            finally:
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
            if code != 0:
                raise RuntimeError("remote Codex exit was not confirmed clean")
        self._process = None
        if self._stderr is not None:
            await self._stderr
            self._stderr = None

    async def checkpoint(self) -> tuple[bytes, bytes]:
        async with self._checkpoint_lock:
            if self._closing or self._closed or not self._owned or self._checkpoint_started:
                raise RuntimeError("worker cannot checkpoint")
            self._checkpoint_started = True
            try:
                await self._stop_server()
                if self._relay is not None:
                    await self._relay.aclose()
                await self._quiesce()
                workspace = await self._export("/workspace")
                home = await self._export(ENVIRONMENT["CODEX_HOME"])
                return validate_archive(workspace), validate_archive(home)
            except BaseException:
                await self.aclose()
                raise

    async def _quiesce(self):
        # Detached tool descendants must not write across the two exports. PID1
        # is the attested Docker init; every other pre-existing process is stopped.
        # Fresh exporter execs run trusted Python from the read-only image only.
        await self._run("exec", self.name, "/usr/local/bin/python", "-I", "-c", _QUIESCE)

    async def _export(self, path: str) -> bytes:
        if path not in {"/workspace", ENVIRONMENT["CODEX_HOME"]}:
            raise ValueError("invalid export root")
        process = await self._spawn(
            "exec", self.name, "/usr/local/bin/python", "-I", "-u", "-c", _EXPORT, path
        )
        assert process.stdout is not None and process.stderr is not None
        stderr = asyncio.create_task(self._discard_stderr(process.stderr))
        try:
            async with asyncio.timeout(EXPORT_TIMEOUT):
                output = bytearray()
                while chunk := await process.stdout.read(65536):
                    if len(output) + len(chunk) > MAX_ARCHIVE_BYTES:
                        raise ValueError("worker checkpoint exceeds archive limit")
                    output.extend(chunk)
                if await process.wait() != 0:
                    raise ValueError("worker checkpoint exporter failed")
                return validate_archive(bytes(output))
        except BaseException:
            outcome = await run_persistent_cleanup(
                lambda: CodexSupervisor._terminate_and_reap(process),
                task_name="worker-export-client-reap",
            )
            if outcome.error is not None:
                raise outcome.error from None
            raise
        finally:
            stderr.cancel()
            await asyncio.gather(stderr, return_exceptions=True)

    async def _cleanup(self):
        errors = []
        try:
            await super()._cleanup()
        except BaseException as exc:  # noqa: BLE001 -- attempt every resource and retain failure
            errors.append(exc)
        if self._relay is not None:
            try:
                await self._relay.aclose()
                self._relay = None
            except BaseException as exc:  # noqa: BLE001 -- retain resource for retry
                errors.append(exc)
        if self._bridge is not None:
            try:
                await CodexSupervisor._terminate_and_reap(self._bridge)
                self._bridge = None
            except BaseException as exc:  # noqa: BLE001 -- retain resource for retry
                errors.append(exc)
        if self._bridge_stderr is not None:
            self._bridge_stderr.cancel()
            await asyncio.gather(self._bridge_stderr, return_exceptions=True)
            self._bridge_stderr = None
        if errors:
            raise errors[0]


async def remove_recorded_worker(name: str) -> None:
    """Recover only exact broker-generated names with verified Docker ownership."""
    if not isinstance(name, str) or not re.fullmatch(r"kairos-worker-[0-9a-f]{32}", name):
        raise ValueError("invalid recorded worker name")
    worker = DockerWorker(Path("/"), image="recovery-unused")
    worker.name = name
    worker._owned = True
    await worker.aclose()
