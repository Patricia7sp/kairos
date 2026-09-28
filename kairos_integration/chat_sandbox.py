"""Sandbox de execução por sessão para o turno comum (P3, Opção 2).

O `bash`/`terminal` do Chat — hoje executado em processo no host por
`kairos_tools.builtin.bash_tool` — pode rodar dentro do worker Docker atestado
(`DockerWorker`) quando habilitado por `config.yaml → chat.sandboxed_bash`.
Um worker ativo por conversa, criado lazy no primeiro uso, reutilizado entre
turnos. **Nunca há fallback para o host**: sandbox indisponível é erro nomeado
(`unavailable`) e o comando não toca a máquina hospedeira.

A fronteira é deliberada (D-RT.4): o `/workspace` do container nasce vazio — o
turno comum não carrega workspace de projeto no envelope —, sem mounts do host,
rede `none`; nada que roda dentro volta ao host. O `bash` sandboxed não vê os
arquivos do host; o acesso a arquivos continua pelos tools de arquivo
aprovados, no host.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from kairos_runtime.execution import load_execution_limits
from kairos_tools.registry import SANDBOX_ALIASES, registry

WORKER_IMAGE_DEFAULT = "kairos:external-sandbox"
WORKSPACE = "/workspace"
#: Mesmo default do `bash_tool` do host (`kairos_tools/builtin.py`).
BASH_DEFAULT_TIMEOUT = 60

__all__ = [
    "BASH_DEFAULT_TIMEOUT",
    "WORKSPACE",
    "ChatBashSandbox",
    "ChatSandboxError",
    "ChatSandboxUnavailable",
    "parse_chat_sandbox_config",
]


class ChatSandboxError(RuntimeError):
    """Falha da fronteira atestada que não vira sucesso e não degrada ao host."""

    status = "failed"


class ChatSandboxUnavailable(ChatSandboxError):
    """A fronteira atestada não existe: sem worker não há execução."""

    status = "unavailable"


def parse_chat_sandbox_config(raw: object) -> bool:
    """`chat.sandboxed_bash` de config.yaml, fail-closed.

    `None`/ausente → desligado. Qualquer valor que não seja booleano levanta
    `ValueError` — um sandbox que pode ser desligado por tipo errado é uma
    sugestão, não uma fronteira.
    """
    if raw is None:
        return False
    if type(raw) is not bool:
        raise ValueError("chat.sandboxed_bash deve ser um booleano")
    return raw


def _bash_argv(command: str) -> list[str]:
    return ["/bin/sh", "-c", command]


def _bash_shape(native: Mapping[str, Any]) -> dict[str, Any]:
    """Traduz `command/exec` para o shape do `bash_tool` (contrato inalterado)."""
    if (
        type(native.get("exitCode")) is not int
        or not isinstance(native.get("stdout"), str)
        or not isinstance(native.get("stderr"), str)
    ):
        raise ChatSandboxError("resposta do sandbox inválida")
    return {
        "stdout": native["stdout"],
        "stderr": native["stderr"],
        "exit_code": native["exitCode"],
        "success": native["exitCode"] == 0,
    }


def _normalize_cwd(cwd: str) -> str:
    """Caminho de trabalho dentro do `/workspace`, sem escape.

    O container não exporta caminhos do host; `cwd` fora do `/workspace` é
    argumento inválido no contexto da fronteira (nunca chamada no host).
    """
    if not isinstance(cwd, str) or not cwd:
        raise TypeError("cwd inválido no sandbox")
    normalized = os.path.normpath(cwd)
    if normalized == WORKSPACE or normalized.startswith(WORKSPACE + "/"):
        return normalized
    raise TypeError("cwd fora do workspace do sandbox")


class ChatBashSandbox:
    """Execução de `bash`/`terminal` no worker atestado, um por conversa.

    `worker_factory` recebe um projeto vazio e deve devolver um worker async
    context-manager com `execute(command, *, cwd, limits)` e `aclose()`
    (`DockerWorker` em produção; double nos testes). A busca de `cwd` e a
    aplicação de `code_execution.*` são feitas aqui; o `DockerWorker` só precisa
    repassar o `cwd` validado à chamada `command/exec`.
    """

    def __init__(
        self,
        home: Path,
        *,
        worker_factory: Callable[[Path], Awaitable[Any]] | None = None,
    ) -> None:
        self._limits = load_execution_limits(home)
        if worker_factory is None:
            image = os.environ.get("KAIROS_RUNTIME_WORKER_IMAGE", WORKER_IMAGE_DEFAULT)
            self._factory: Callable[[Path], Awaitable[Any]] = lambda project: _start_worker(
                project, image=image
            )
        else:
            self._factory = worker_factory
        self._empty = tempfile.TemporaryDirectory(prefix="kairos-chat-sandbox-")
        self._project = Path(self._empty.name) / "workspace"
        self._project.mkdir(mode=0o700)
        self._workers: dict[str, Any] = {}
        self._closed = False

    async def dispatch(
        self,
        conversation_id: str,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        """Despacho pela costura `execute=` do `execute_chat_tool`.

        `bash`/`terminal` (via `SANDBOX_ALIASES`) roda no worker da conversa;
        qualquer outro nome delegado ao `registry.dispatch` do host, inalterado.
        """
        resolved = SANDBOX_ALIASES.get(name, name)
        if resolved != "bash":
            return registry.dispatch(name, arguments)
        arguments = dict(arguments or {})
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise TypeError("bash requer command")
        cwd = _normalize_cwd(arguments["cwd"]) if arguments.get("cwd") is not None else WORKSPACE
        raw_timeout = arguments.get("timeout")
        if raw_timeout is not None and (type(raw_timeout) is not int or raw_timeout < 1):
            raise TypeError("timeout inválido")
        timeout = min(
            raw_timeout if raw_timeout is not None else BASH_DEFAULT_TIMEOUT,
            self._limits.timeout_seconds,
        )
        limits = replace(self._limits, timeout_seconds=timeout)
        worker = await self._worker_for(conversation_id)
        try:
            native = await worker.execute(_bash_argv(command), cwd=cwd, limits=limits)
        except ValueError as exc:
            # Argumento que o sandbox não aceita (ex.: NUL no comando): não é
            # indisponibilidade, é argumento inválido — e o worker pode ter sido
            # fechado pelo próprio `execute`; descarta o cache do mesmo jeito.
            self._workers.pop(conversation_id, None)
            raise TypeError(str(exc)) from None
        except BaseException as exc:
            self._workers.pop(conversation_id, None)
            raise ChatSandboxUnavailable(str(exc)) from exc
        if native.get("exitCode") != 0:
            # O `DockerWorker` fecha o worker em todo comando com saída não-zero
            # (`no children survive`); um worker morto não é reutilizável.
            self._workers.pop(conversation_id, None)
        return _bash_shape(native)

    async def _worker_for(self, conversation_id: str) -> Any:
        if self._closed:
            raise ChatSandboxUnavailable("sandbox encerrado")
        worker = self._workers.get(conversation_id)
        if worker is not None:
            return worker
        try:
            worker = await self._factory(self._project)
            await worker.__aenter__()
        except BaseException as exc:
            raise ChatSandboxUnavailable("worker atestado indisponível") from exc
        self._workers[conversation_id] = worker
        return worker

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []
        for worker in list(self._workers.values()):
            try:
                await worker.aclose()
            except BaseException as exc:  # noqa: BLE001 - retém para agregação
                errors.append(exc)
        self._workers.clear()
        self._empty.cleanup()
        if errors:
            raise errors[0]


async def _start_worker(project: Path, *, image: str) -> Any:
    from kairos_runtime.experimental.docker_worker import DockerWorker

    return DockerWorker(project, image=image, writable=False)
