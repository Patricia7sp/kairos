"""Execução limitada e segura da fonte de um monitor de agendamento.

`_reversa_sdd/cron/` §monitor — a fonte (`monitor_script`) roda **antes** da
decisão do agente. A execução é deliberadamente estreita:

- **Sem shell** (`shell=False`): a linha é dividida por `shlex.split` e vira
  argv. Operadores de shell são argumentos literais, não comandos.
- **Ambiente mínimo**: só `HOME`, `PATH`, localização e fuso. `KAIROS_HOME`,
  `KAIROS_WEB_TOKEN` e a passphrase do cofre **não** são passados — um script
  de fonte é código confiável, mas não precisa de segredos para observar a
  fonte, e o env é o primeiro lugar onde um vazamento aparece.
- **Orçamentos rígidos**: prazo (default 30 s, máx. 120 s), saída (default
  64 KiB, máx. 256 KiB) e kill do grupo de processos no timeout — sem órfãos.
- **Falha é ERRO, nunca saída**: timeout, código de saída não nulo, processo
  inexistente ou saída ilegível não produzem conteúdo para comparar. O hash
  armazenado fica intocado (ver `kairos_cron.monitor`).
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MAX_OUTPUT_CHARS",
    "MAX_SCRIPT_CHARS",
    "MAX_TIMEOUT_SECONDS",
    "SourceResult",
    "run_script",
    "validate_script",
]

MAX_SCRIPT_CHARS = 4000
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_TIMEOUT_SECONDS = 120.0
DEFAULT_MAX_OUTPUT_CHARS = 65_536
MAX_OUTPUT_CHARS = 262_144


@dataclass(frozen=True)
class SourceResult:
    ok: bool
    output: str | None = None
    #: Código fixo da causa, sem conteúdo do script nem do ambiente.
    error: str | None = None
    #: Mensagem curta e declarável (nunca a saída bruta do processo).
    detail: str | None = None


def validate_script(script: str) -> None:
    """Valida antes de tocar em qualquer armazenamento."""
    if not isinstance(script, str) or not 0 < len(script.strip()) <= MAX_SCRIPT_CHARS:
        raise ValueError(f"comando de monitor deve ter de 1 a {MAX_SCRIPT_CHARS} caracteres")
    try:
        argv = shlex.split(script)
    except ValueError as exc:
        raise ValueError("comando de monitor tem aspas ou escape inválidos") from exc
    if not argv:
        raise ValueError("comando de monitor vazio")


def _minimal_env(home: Path) -> dict[str, str]:
    path = os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    return {
        "HOME": str(home),
        "PATH": path,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "TZ": os.environ.get("TZ", "UTC"),
    }


def run_script(  # noqa: PLR0912 - cascade de classificação de erro; cada degrau devolve e sai
    command: str,
    *,
    home: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> SourceResult:
    """Executa a fonte uma vez e devolve saída ou erro classificado."""
    try:
        validate_script(command)
    except ValueError as exc:
        return SourceResult(False, error="invalid_script", detail=str(exc))

    if type(max_output_chars) is not int or not 1024 <= max_output_chars <= MAX_OUTPUT_CHARS:
        return SourceResult(False, error="invalid_script", detail="limite de saída inválido")
    if type(timeout) not in (int, float) or not 1 <= timeout <= MAX_TIMEOUT_SECONDS:
        return SourceResult(False, error="invalid_script", detail="prazo de execução inválido")

    argv = shlex.split(command)
    if not argv or shutil.which(argv[0]) is None:
        return SourceResult(False, error="unavailable", detail="executável da fonte não encontrado")

    try:
        process = subprocess.Popen(  # noqa: S603 -- argv explícito do operador, sem shell
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=home,
            env=_minimal_env(home),
            start_new_session=True,
        )
    except FileNotFoundError:
        return SourceResult(False, error="unavailable", detail="executável da fonte não encontrado")
    except OSError:
        return SourceResult(False, error="failed", detail="não foi possível executar a fonte")

    try:
        try:
            saida, erros = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # O grupo de processos é morto por inteiro — sem órfãos deixados
            # para trás por um script que bifurcou.
            try:
                os.killpg(process.pid, 15)
            except ProcessLookupError:
                pass
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                process.communicate()
            return SourceResult(
                False, error="timeout", detail="a fonte excedeu o prazo e foi interrompida"
            )
    except OSError:
        return SourceResult(False, error="failed", detail="não foi possível executar a fonte")

    if process.returncode != 0:
        return SourceResult(
            False, error="failed", detail=f"a fonte saiu com código {process.returncode}"
        )

    conteudo = ""
    if saida:
        conteudo += saida
    if erros:
        conteudo += erros
    if len(conteudo) > max_output_chars:
        conteudo = conteudo[:max_output_chars]
    return SourceResult(True, output=conteudo)
