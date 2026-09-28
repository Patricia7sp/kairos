"""Limites de execução de código no sandbox — `code_execution.*` (P3).

A spec (`_reversa_sdd/tools/requirements.md:39` e `:73-77`) define a execução
em sandbox com limites de recurso configuráveis via `config.yaml →
code_execution.*`: **timeout 300 s, 50 chamadas de ferramenta, 50 KB de stdout,
10 KB de stderr**. Este módulo traduz essas entradas em um objeto imutável e
fail-closed.

Fail-closed aqui tem o mesmo peso de todo o resto: `config.yaml` com
`code_execution` malformado não cala — levanta `ValueError` com o motivo. O core
é um cinto estreito; um limite que pode ser desligado por um campo com tipo
errado não é limite, é sugestão.

O que este módulo **não** faz: impor tetos nas chamadas internas do Codex
(que são regidas pela atestação do container — memória/pids/cpu/no-new-privs).
Ele rege a fronteira de execução que o Kairos controla (`DockerWorker.execute`).
A justificativa da separação está em `docs/decisoes.md` (D-RT.3).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_MAX_STDERR_BYTES",
    "DEFAULT_MAX_STDOUT_BYTES",
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_STDERR_BYTES",
    "MAX_STDOUT_BYTES",
    "MAX_TIMEOUT_SECONDS",
    "ExecutionLimits",
    "load_execution_limits",
    "parse_execution_limits",
]

#: Padrões da spec (`tools/requirements.md`).
DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_MAX_STDOUT_BYTES = 50_000
DEFAULT_MAX_STDERR_BYTES = 10_000

#: Tetos absolutos: sanidade, não política. Evitam que um `timeout_seconds`
#: gigante ou um cap de bytes desligue o ciclo de vida do worker.
MAX_TIMEOUT_SECONDS = 3600
MAX_STDOUT_BYTES = 1_048_576
MAX_STDERR_BYTES = 1_048_576

#: Alias da spec (`max_tool_calls`) — a spec a lista, mas nenhum executor do
#: Kairos conta chamadas de ferramenta hoje; fica como constante de
#: rastreabilidade, não como enforcer (ver D-RT.3).
MAX_TOOL_CALLS = 50

_CONFIG_KEY = "code_execution"


@dataclass(frozen=True)
class ExecutionLimits:
    """Limites imutáveis de uma execução no sandbox.

    `timeout_seconds` também rege `DockerWorker.execute` (conversão para
    `timeoutMs`); `max_stdout_bytes` rege o `outputBytesCap` do `command/exec`
    (o fio do Codex expõe um único cap — o teto de stdout é o limite aplicado;
    ver D-RT.3). `max_stderr_bytes` é o teto registrado e conferido no
    resultado.
    """

    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_stdout_bytes: int = DEFAULT_MAX_STDOUT_BYTES
    max_stderr_bytes: int = DEFAULT_MAX_STDERR_BYTES


def parse_execution_limits(raw: object) -> ExecutionLimits:
    """Traduz a entrada de `config.yaml` para `ExecutionLimits`, fail-closed.

    `None`/ausente → padrões da spec. Qualquer valor inválido (não-dict, tipo
    errado, não positivo, acima do teto) levanta `ValueError` nomeado — nunca
    calla para padrões em silêncio.
    """
    if raw is None:
        return ExecutionLimits()
    if not isinstance(raw, Mapping):
        raise ValueError("code_execution deve ser um mapeamento em config.yaml")
    confuso = [
        key for key in raw if key not in {"timeout_seconds", "max_stdout_bytes", "max_stderr_bytes"}
    ]
    if confuso:
        raise ValueError(f"code_execution com chave desconhecida: {confuso[0]}")
    timeout = _positivo(
        raw.get("timeout_seconds"),
        "code_execution.timeout_seconds",
        MAX_TIMEOUT_SECONDS,
        DEFAULT_TIMEOUT_SECONDS,
    )
    stdout = _positivo(
        raw.get("max_stdout_bytes"),
        "code_execution.max_stdout_bytes",
        MAX_STDOUT_BYTES,
        DEFAULT_MAX_STDOUT_BYTES,
    )
    stderr = _positivo(
        raw.get("max_stderr_bytes"),
        "code_execution.max_stderr_bytes",
        MAX_STDERR_BYTES,
        DEFAULT_MAX_STDERR_BYTES,
    )
    return ExecutionLimits(timeout, stdout, stderr)


def load_execution_limits(home: Path) -> ExecutionLimits:
    """Limites do home, lendo `code_execution` em `<home>/config.yaml`.

    `config.yaml` ausente → padrões da spec. `code_execution` ausente →
    padrões. `code_execution` malformado → `ValueError` (fail-closed; ao
    contrário de `kairos_cli.config.load_config`, aqui silêncio seria fingir
    que a fronteira tem limites que não tem).
    """
    path = Path(home) / "config.yaml"
    if not path.is_file():
        return ExecutionLimits()
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"config.yaml ilegível: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("config.yaml deve conter um mapeamento de topo")
    return parse_execution_limits(raw.get(_CONFIG_KEY))


def _positivo(value: object, key: str, teto: int, padrao: int) -> int:
    if value is None:
        return padrao
    if type(value) is not int or value < 1:
        raise ValueError(f"{key} deve ser um inteiro positivo")
    if value > teto:
        raise ValueError(f"{key} acima do teto de {teto}")
    return value
