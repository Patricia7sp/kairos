"""Ferramenta `git` — subconjunto delimitado sobre o git do host.

Expõe exatamente `status`, `diff`, `log` e `commit` com processo sem shell,
hooks/assinatura desligados, pathspecs literais e env `GIT_*` esterilizado
(mesmo endurecimento de `kairos_cli.runtime_delivery_git`). O gate de exibição
é o toolset `git` (requisito: git instalado); o gate de mutação é por
subcomando no Chat (`kairos_integration.chat_tools.needs_tool_approval`) —
`commit` pede aprovação por turno, leitura flui sem prompt.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

from kairos_tools.registry import ToolRegistry, registry

#: Subcomandos aceitos. Qualquer outro é recusado nomeado — a ferramenta não
#: é um shell para o git completo, é um subconjunto seguro e predizível.
_ALLOWED_SUBCOMMANDS: frozenset[str] = frozenset({"status", "diff", "log", "commit"})

MAX_MESSAGE_CHARS = 4000
MAX_LOG_LIMIT = 200
DEFAULT_LOG_LIMIT = 20
GIT_TIMEOUT_SECONDS = 30.0


def _git_prefix() -> list[str]:
    return [
        "git",
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
        "--literal-pathspecs",
    ]


def _clean_env() -> dict[str, str]:
    """Ambiente sem herança `GIT_*` — objetos, filtros e formato de saída não
    podem ser redirecionados por um env residual do processo pai."""
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git_available() -> bool:
    return shutil.which("git") is not None


def _error(message: str) -> dict[str, Any]:
    return {"error": message, "exit_code": -1, "success": False}


def _valid_paths(paths: list[str] | None) -> list[str] | None:
    if not paths:
        return None
    valid: list[str] = []
    for raw in paths:
        if not isinstance(raw, str) or not raw:
            return None
        if raw.startswith("-") or "\x00" in raw:
            return None
        path = Path(raw)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            return None
        valid.append(raw)
    return valid or None


async def _run(argv: list[str], cwd: str | None) -> dict[str, Any]:
    work_dir = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not Path(work_dir).is_dir():
        return _error(f"diretório inexistente: {work_dir}")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=work_dir,
        env=_clean_env(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=GIT_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        return _error(f"git expirou após {GIT_TIMEOUT_SECONDS:.0f}s")
    return {
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": stderr.decode("utf-8", errors="replace"),
        "exit_code": proc.returncode,
        "success": proc.returncode == 0,
    }


async def _run_commit(
    *,
    cwd: str | None,
    target: list[str] | None,
    message: str | None,
) -> dict[str, Any]:
    if not message or not message.strip():
        return _error("commit exige message não vazia")
    if len(message) > MAX_MESSAGE_CHARS:
        return _error(f"message longa demais (máx. {MAX_MESSAGE_CHARS} caracteres)")
    if target:
        staged_result = await _run([*_git_prefix(), "add", "--", *target], cwd)
        if not staged_result["success"]:
            return {
                **_error("git add falhou; correções não confirmadas"),
                **staged_result,
            }
    return await _run([*_git_prefix(), "commit", "-m", message], cwd)


async def _run_log(
    *, cwd: str | None, target: list[str] | None, limit: int | None
) -> dict[str, Any]:
    count = limit if limit is not None else DEFAULT_LOG_LIMIT
    if not 1 <= count <= MAX_LOG_LIMIT:
        return _error(f"limit deve estar entre 1 e {MAX_LOG_LIMIT}")
    argv = [*_git_prefix(), "log", "-n", str(count), "--oneline", "--decorate"]
    if target:
        argv.extend(["--", *target])
    return await _run(argv, cwd)


async def _run_diff(*, cwd: str | None, target: list[str] | None, staged: bool) -> dict[str, Any]:
    argv = [*_git_prefix(), "diff"]
    if staged:
        argv.append("--cached")
    if target:
        argv.extend(["--", *target])
    return await _run(argv, cwd)


async def _run_status(*, cwd: str | None, target: list[str] | None) -> dict[str, Any]:
    argv = [*_git_prefix(), "status", "--short"]
    if target:
        argv.extend(["--", *target])
    return await _run(argv, cwd)


async def git_tool(
    *,
    subcommand: str,
    cwd: str | None = None,
    message: str | None = None,
    paths: list[str] | None = None,
    limit: int | None = None,
    staged: bool = False,
) -> dict[str, Any]:
    """Opera o repositório git indicado nos subcomandos aceitos."""
    if subcommand not in _ALLOWED_SUBCOMMANDS:
        return _error(f"subcomando não suportado: {subcommand}")
    if not _git_available():
        return _error("git não está disponível neste host")
    target = _valid_paths(paths)
    if paths and target is None:
        return _error("path deve ser relativo, no mesmo repositório, e não começar com '-'")

    runners = {
        "commit": _run_commit,
        "log": _run_log,
        "diff": _run_diff,
        "status": _run_status,
    }
    kwargs: dict[str, Any] = {"cwd": cwd, "target": target}
    if subcommand == "commit":
        kwargs["message"] = message
    elif subcommand == "log":
        kwargs["limit"] = limit
    elif subcommand == "diff":
        kwargs["staged"] = staged
    return await runners[subcommand](**kwargs)


def register_git_tool(reg: ToolRegistry | None = None) -> None:
    """Registra `git` no toolset próprio, gated pela presença do binário."""
    r = reg or registry
    r.register_toolset("git", requirement=_git_available)
    r.register(
        name="git",
        handler=git_tool,
        schema={
            "type": "function",
            "function": {
                "name": "git",
                "description": (
                    "Opera o repositório git indicado: status, diff, log e commit. "
                    "commit altera o repositório e exige aprovação do usuário."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "subcommand": {
                            "type": "string",
                            "enum": ["status", "diff", "log", "commit"],
                            "description": "A operação a executar. commit exige aprovação.",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Diretório do repositório (opcional; padrão: diretório atual)",
                        },
                        "message": {
                            "type": "string",
                            "description": "Mensagem do commit (obrigatória em commit)",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Arquivos alvo, relativos ao repositório (opcional)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Número de commits em log (padrão 20, máx. 200)",
                        },
                        "staged": {
                            "type": "boolean",
                            "description": "Em diff, mostrar apenas o índice (staged)",
                        },
                    },
                    "required": ["subcommand"],
                    "additionalProperties": False,
                },
            },
        },
        toolset="git",
        max_result_size_chars=100_000,
    )


#: Registra por padrão — mesmo padrão do `builtin` e do `memory`.
register_git_tool()
