"""Launcher confiável no host: um worker offline descartável por contexto.

Não registrar este protótipo no host de produção. Docker é autoridade do
launcher; nenhuma interface Docker ou credencial é entregue ao agente.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from kairos_providers._async_cleanup import run_persistent_cleanup

from ..codex_rpc import CodexRpc
from ..supervisor import EXPECTED_CODEX_VERSION, CodexSupervisor
from .snapshot import snapshot_project
from .worker_policy import ENVIRONMENT, IMAGE_ENV_KEYS, LABEL, WorkerPolicy

EXTERNAL_POLICY = {"type": "externalSandbox", "networkAccess": "restricted"}
KERNEL_PROBE = """
import json, os
from pathlib import Path
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
print(json.dumps({
    'uid': os.getuid(),
    'status': {k: status[k].strip() for k in ('CapEff', 'CapPrm', 'CapBnd', 'NoNewPrivs', 'Seccomp')},
    'apparmor': Path('/proc/self/attr/current').read_text().strip(),
    'memory': Path('/sys/fs/cgroup/memory.max').read_text().strip(),
    'swap': Path('/sys/fs/cgroup/memory.swap.max').read_text().strip(),
    'pids': Path('/sys/fs/cgroup/pids.max').read_text().strip(),
    'cpu': Path('/sys/fs/cgroup/cpu.max').read_text().strip(),
    'interfaces': sorted(p.name for p in Path('/sys/class/net').iterdir()),
}))
"""


class DockerWorker:
    """Async context with explicit, cancellation-safe container ownership."""

    def __init__(
        self, project: Path, *, image: str, writable: bool = False, sandbox: bool | None = None
    ):
        if not image or image.startswith("-") or type(writable) is not bool:
            raise ValueError("imagem ou modo inválido")
        if sandbox is not None and type(sandbox) is not bool:
            raise ValueError("modo sandbox inválido")
        if sandbox is None:
            sandbox = os.environ.get("KAIROS_WORKER_SANDBOX") == "1"
        self.project = project
        self.image = image
        self.writable = writable
        self.sandbox = sandbox
        self.name = "kairos-worker-" + uuid.uuid4().hex
        self._config = tempfile.TemporaryDirectory(prefix="kairos-docker-client-")
        docker = shutil.which("docker")
        if docker is None:
            self._config.cleanup()
            raise RuntimeError("Docker CLI não encontrado")
        self._docker = (
            docker,
            "--config",
            self._config.name,
            "--host",
            "unix:///var/run/docker.sock",
        )
        self._owned = False
        self._closed = False
        self._closing = False
        self._creation_uncertain = False
        self._process: asyncio.subprocess.Process | None = None
        self._stderr: asyncio.Task | None = None
        self._rpc: CodexRpc | None = None
        self.policy: WorkerPolicy | None = None
        self.evidence: dict[str, Any] = {}
        self._close_lock = asyncio.Lock()

    async def __aenter__(self):
        if self._closing or self._closed or self._owned:
            raise RuntimeError("worker não pode ser reutilizado")
        try:
            async with asyncio.timeout(45):
                await self._start()
            return self
        except BaseException:
            await self.aclose()
            raise

    async def __aexit__(self, *_exc):
        await self.aclose()

    async def _start(self):
        # Freeze before any container exists. No source path is ever mounted.
        archive = snapshot_project(self.project)
        await self._prepare(archive)
        await self._start_server([])

    async def _prepare(self, archive: bytes):
        _, raw, _ = await self._run("image", "inspect", self.image)
        image = json.loads(raw)[0]
        config = image.get("Config", {})
        if config.get("Volumes") or config.get("ExposedPorts") or config.get("OnBuild"):
            raise ValueError("imagem de worker contém recursos inesperados")
        if any(entry.partition("=")[0] not in IMAGE_ENV_KEYS for entry in config.get("Env", [])):
            raise ValueError("imagem de worker contém ambiente inesperado")
        self.policy = WorkerPolicy(image["Id"], self.name, self.writable, self.sandbox)
        await self._create()
        await self._run("start", self.name)
        _, inspected, _ = await self._run("inspect", self.name)
        self.policy.validate(json.loads(inspected)[0])
        await self._run("exec", self.name, "mkdir", "-m", "0700", ENVIRONMENT["CODEX_HOME"])
        importer = "10000:10000" if self.writable else "0:0"
        await self._run(
            "exec",
            "-i",
            "--user",
            importer,
            self.name,
            "tar",
            "--extract",
            "--no-same-owner",
            "--no-same-permissions",
            "-C",
            "/workspace",
            input_data=archive,
        )
        _, raw, _ = await self._run(
            "exec", self.name, "/usr/local/bin/python", "-I", "-c", KERNEL_PROBE
        )
        self.evidence = json.loads(raw)
        self._validate_kernel(self.evidence)
        _, version, _ = await self._run("exec", self.name, "codex", "--version")
        if version.decode().strip() != EXPECTED_CODEX_VERSION:
            raise ValueError("versão Codex incompatível")

    async def _start_server(self, args: list[str], *, experimental_api=False):
        self._process = await self._spawn(
            "exec",
            "-i",
            self.name,
            "codex",
            "app-server",
            *args,
            "--listen",
            "stdio://",
        )
        assert self._process.stdout is not None and self._process.stdin is not None
        assert self._process.stderr is not None
        self._stderr = asyncio.create_task(self._discard_stderr(self._process.stderr))
        self._rpc = CodexRpc(
            self._process.stdout,
            self._process.stdin,
            generation=uuid.uuid4().hex,
        )
        await self._rpc.start()
        initialized = await self._rpc.call(
            "initialize",
            {
                "clientInfo": {"name": "kairos-sandbox-prototype", "version": "0.1.0"},
                **({"capabilities": {"experimentalApi": True}} if experimental_api else {}),
            },
        )
        if initialized.get("codexHome") != ENVIRONMENT["CODEX_HOME"]:
            raise ValueError("CODEX_HOME divergente")
        if initialized.get("platformOs") != "linux":
            raise ValueError("plataforma incompatível")
        await self._rpc.notify("initialized")

    async def _create(self):
        self._owned = True
        self._creation_uncertain = True

        async def complete_creation():
            await self._run(*self.policy.create_args())
            self._creation_uncertain = False

        # Cancellation of the caller must not cut off a daemon create reply.
        outcome = await run_persistent_cleanup(
            complete_creation, task_name="external-worker-create-completion"
        )
        if outcome.error is not None:
            raise outcome.error
        if outcome.cancellation is not None:
            raise outcome.cancellation

    @staticmethod
    def _validate_kernel(evidence):
        expected = {
            "uid": 10000,
            "status": {
                "CapEff": "0000000000000000",
                "CapPrm": "0000000000000000",
                "CapBnd": "0000000000000000",
                "NoNewPrivs": "1",
                "Seccomp": "2",
            },
            "apparmor": "docker-default (enforce)",
            "memory": str(512 * 1024**2),
            "swap": "0",
            "pids": "128",
            "cpu": "100000 100000",
            "interfaces": ["lo"],
        }
        if evidence != expected:
            raise ValueError("isolamento de kernel divergente; worker recusado")

    async def execute(self, command: list[str], *, timeout_ms: int = 5000) -> dict[str, Any]:
        """Run argv inside the attested boundary, without a model/API call."""
        if self._closing or self._closed or self._rpc is None or not self._owned:
            raise RuntimeError("worker não está ativo")
        if (
            not command
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or type(timeout_ms) is not int
            or not 1 <= timeout_ms <= 30000
        ):
            raise ValueError("comando ou timeout inválido")
        try:
            async with asyncio.timeout(timeout_ms / 1000 + 5):
                result = await self._rpc.call(
                    "command/exec",
                    {
                        "command": command,
                        "cwd": "/workspace",
                        "timeoutMs": timeout_ms,
                        "outputBytesCap": 65536,
                        "sandboxPolicy": dict(EXTERNAL_POLICY),
                    },
                )
            if (
                type(result.get("exitCode")) is not int
                or not isinstance(result.get("stdout"), str)
                or not isinstance(result.get("stderr"), str)
            ):
                raise ValueError("resposta de comando inválida")
            # Codex reports server timeouts as an ordinary nonzero command result.
            # Conservatively discard on every command failure; no children survive.
            if result["exitCode"] != 0:
                await self.aclose()
            return result
        except BaseException:
            # An abandoned RPC cannot leave a process running in the background.
            await self.aclose()
            raise

    async def aclose(self):
        self._closing = True
        outcome = await run_persistent_cleanup(self._cleanup, task_name="external-worker-close")
        if outcome.error is not None:
            raise outcome.error
        if outcome.cancellation is not None:
            raise outcome.cancellation

    async def _cleanup(self):
        async with self._close_lock:
            if self._closed:
                return
            errors = []
            # Try every resource even if Docker is unavailable. Only clear ownership
            # after confirmed removal; refuse further commands from the first close.
            if self._owned:
                try:
                    await self._remove()
                except BaseException as exc:  # noqa: BLE001 - tentar os demais recursos
                    errors.append(exc)
            if self._rpc is not None:
                try:
                    await self._rpc.aclose()
                    self._rpc = None
                except BaseException as exc:  # noqa: BLE001 - reter para retry
                    errors.append(exc)
            if self._process is not None:
                try:
                    await CodexSupervisor._terminate_and_reap(self._process)
                    self._process = None
                except BaseException as exc:  # noqa: BLE001 - reter para retry
                    errors.append(exc)
            if self._stderr is not None:
                self._stderr.cancel()
                await asyncio.gather(self._stderr, return_exceptions=True)
                self._stderr = None
            if errors:
                raise errors[0]
            self._config.cleanup()
            self._closed = True

    async def _remove(self):
        # Labels avoid removing an unrelated container on an improbable name collision.
        code, raw, _ = await self._run("inspect", self.name, check=False)
        if code == 0:
            inspected = json.loads(raw)[0]
            if inspected.get("Config", {}).get("Labels", {}).get(LABEL) != "prototype":
                raise RuntimeError("ownership divergente durante limpeza")
            await self._run("rm", "--force", "--volumes", self.name)
            self._creation_uncertain = False
        if self._creation_uncertain:
            raise RuntimeError(f"criação não confirmada; ownership retido: {self.name}")
        _, remaining, _ = await self._run(
            "container",
            "ls",
            "-a",
            "--filter",
            f"name=^/{self.name}$",
            "--format",
            "{{.Names}}",
        )
        if remaining.strip():
            raise RuntimeError("worker ainda existe após limpeza")
        self._owned = False

    async def _spawn(self, *args):
        return await asyncio.create_subprocess_exec(
            *self._docker,
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": os.defpath, "LANG": "C.UTF-8"},
        )

    async def _run(self, *args, input_data=None, check=True):
        process = await self._spawn(*args)
        try:
            async with asyncio.timeout(20):
                stdout, stderr = await process.communicate(input_data)
        except BaseException:
            outcome = await run_persistent_cleanup(
                lambda: CodexSupervisor._terminate_and_reap(process),
                task_name="external-docker-client-reap",
            )
            if outcome.error is not None:
                raise outcome.error from None
            raise
        if check and process.returncode:
            # Never interpolate daemon output; it may contain host metadata.
            raise RuntimeError(f"Docker falhou na operação {args[0]} (rc={process.returncode})")
        return process.returncode, stdout, stderr

    @staticmethod
    async def _discard_stderr(reader):
        while await reader.read(4096):
            pass
