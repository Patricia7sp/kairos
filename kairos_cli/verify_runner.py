"""Executor da verificação: fases de uma receita e smoke de readiness.

Origem no legado: `agent/verify/runner.py` (hermes_cli/verify_cmd.py),
port scoped do executor que grok-cli usa (install/bootstrap → build → test
→ start em background → poll de readiness → teardown), reimplementado como
subprocesso puro **sem shell**.

**Sem o auxílio do shell (revisão da D-CLI.10).** Os comandos vêm da receita
do projeto (scripts do package.json, alvos do Makefile etc.) e rodam no
checkout do próprio usuário — o mesmo nível de confiança do terminal da
pessoa, e não há guard de ciclo de vida porque não é despacho de modelo nem
de cron. Ainda assim, a auditoria de segurança do repositório (SEC-SRC-004,
gate do CI) proíbe que o `subprocess` rode com o shell ativado por
parâmetro: o executor resolve o comando com `shlex.split` e **recusa
barulhento** comandos com metacaracteres de shell que não consegue honrar
fielmente (`&&`, `;`, `|`, `<`, `>`, globs, expansão `$`, backtick, `~`) —
melhor recusar que executar errado (fail closed). Receitas com encadeamento
pedem uma passada por comando sem operadores no manifesto
`.kairos/environment.json`.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kairos_cli.verify_recipe import Recipe

__all__ = [
    "DEFAULT_PHASE_TIMEOUT",
    "DEFAULT_READY_TIMEOUT",
    "PHASE_ORDER",
    "PhaseResult",
    "ReadinessResult",
    "VerifyResult",
    "command_argv",
    "run_verify",
]

DEFAULT_PHASE_TIMEOUT = 600.0
DEFAULT_READY_TIMEOUT = 60.0
_TAIL_CHARS = 2000
PHASE_ORDER = ("bootstrap", "build", "test")

#: Metacaracteres que o executor não honra sem shell: encadeamento e
#: operadores (`&&`, `||`, `;`, `|`), redirecionamento (`<`, `>`), globs
#: (`*`, `?`, `[`, `]`), expansão (`$`, backtick, `~`, `{}`). A presença de
#: qualquer um deles vira recusa barulhenta, não execução inventada.
_UNSUPPORTED_SHELL = set("&|;<>$`*?[]{}~")


class UnsupportedCommand(ValueError):
    """Comando de receita com metacaractere de shell não suportado."""


def _recusa_metacaractere(command: str, char: str) -> None:
    raise UnsupportedCommand(
        f"comando usa metacaractere de shell não suportado ({char!r}): "
        f"{command!r} — ajuste a receita no manifesto"
    )


def command_argv(command: str) -> list[str]:
    """Resolve um comando de receita em argv executável sem shell.

    A verificação re-tokeniza o comando com estado de aspas para ser fiel ao
    shell: dentro de aspas simples tudo é literal; dentro de aspas duplas só
    ``$`` e backtick continuam especiais; fora de aspas qualquer metacaractere
    da lista recusa. Metacaracteres que o shell interpreta e o executor não
    consegue honrar recusam (``UnsupportedCommand``) em vez de passar o
    comando torto.
    """
    quote: str | None = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote == "'":
            if char == "'":
                quote = None
            continue
        if quote == '"':
            if char == '"':
                quote = None
            elif char in "$`":
                _recusa_metacaractere(command, char)
            continue
        if char in ("'", '"'):
            quote = char
            continue
        if char in _UNSUPPORTED_SHELL:
            _recusa_metacaractere(command, char)
    return shlex.split(command)


@dataclass
class PhaseResult:
    phase: str
    command: str
    exit_code: int | None
    duration: float
    output_tail: str
    timed_out: bool = False
    unsupported: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out and self.unsupported is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "command": self.command,
            "exitCode": self.exit_code,
            "duration": round(self.duration, 3),
            "ok": self.ok,
            "timedOut": self.timed_out,
            "unsupported": self.unsupported,
            "outputTail": self.output_tail,
        }


@dataclass
class ReadinessResult:
    url: str
    ready: bool
    status_code: int | None
    duration: float
    error: str | None = None
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "ready": self.ready,
            "statusCode": self.status_code,
            "duration": round(self.duration, 3),
            "error": self.error,
            "outputTail": self.output_tail,
        }


@dataclass
class VerifyResult:
    recipe_name: str
    phases: list[PhaseResult] = field(default_factory=list)
    readiness: ReadinessResult | None = None

    @property
    def ok(self) -> bool:
        phases_ok = all(p.ok for p in self.phases)
        readiness_ok = self.readiness.ready if self.readiness is not None else True
        return phases_ok and readiness_ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "recipe": self.recipe_name,
            "ok": self.ok,
            "phases": [p.to_dict() for p in self.phases],
            "readiness": self.readiness.to_dict() if self.readiness else None,
        }


def _tail(text: str, limit: int = _TAIL_CHARS) -> str:
    return text[-limit:] if len(text) > limit else text


def _run_phase_command(
    phase: str,
    command: str,
    root: Path,
    timeout: float,
    on_output: Callable[[str], None] | None = None,
) -> PhaseResult:
    started = time.monotonic()
    try:
        argv = command_argv(command)
    except UnsupportedCommand as exc:
        return PhaseResult(
            phase=phase,
            command=command,
            exit_code=None,
            duration=time.monotonic() - started,
            output_tail="",
            unsupported=str(exc),
        )
    try:
        proc = subprocess.run(  # noqa: S603 — comando da receita do próprio projeto; docstring do módulo
            argv,
            cwd=str(root),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
            errors="replace",
            check=False,  # o exit code vira PhaseResult, não exceção
        )
        output = proc.stdout or ""
        exit_code: int | None = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        raw = exc.output
        output = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw or ""
        exit_code = None
        timed_out = True
    duration = time.monotonic() - started
    if on_output and output:
        on_output(output)
    return PhaseResult(
        phase=phase,
        command=command,
        exit_code=exit_code,
        duration=duration,
        output_tail=_tail(output),
        timed_out=timed_out,
    )


def _poll_readiness(
    url: str, timeout: float, interval: float = 1.0
) -> tuple[bool, int | None, str | None]:
    deadline = time.monotonic() + timeout
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310 — URL local construída como http://127.0.0.1:porta+readiness
                return True, resp.status, None
        except urllib.error.HTTPError as exc:
            # O servidor respondeu — está de pé, mesmo em 4xx/5xx.
            return True, exc.code, None
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = str(exc)
        time.sleep(interval)
    return False, None, last_error


def _terminate_process_group(proc: subprocess.Popen) -> None:
    """Encerra o app iniciado e todo o grupo de processos de forma limpa.

    No POSIX o filho é criado com ``start_new_session=True`` para poder
    sinalizar o grupo inteiro; a morte do grupo garante que subprocessos do
    app (workes, dev server children) não fiquem órfãos.
    """
    if proc.poll() is not None:
        return
    killpg = getattr(os, "killpg", None)
    getpgid = getattr(os, "getpgid", None)
    pgid = None
    if killpg is not None and getpgid is not None:
        try:
            pgid = getpgid(proc.pid)
        except (ProcessLookupError, PermissionError):
            pgid = None
    try:
        if pgid is not None and killpg is not None:
            killpg(pgid, signal.SIGTERM)  # windows-footgun: ok — ramo POSIX (killpg verificado)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if pgid is not None and killpg is not None:
                killpg(pgid, signal.SIGKILL)  # windows-footgun: ok — ramo POSIX (killpg verificado)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


def _run_start_phase(
    recipe: Recipe,
    root: Path,
    ready_timeout: float,
    port_override: int | None = None,
) -> ReadinessResult:
    assert recipe.start is not None
    port = port_override or recipe.port or 8000
    url = f"http://127.0.0.1:{port}{recipe.readiness_path}"
    started = time.monotonic()
    try:
        argv = command_argv(recipe.start)
    except UnsupportedCommand as exc:
        return ReadinessResult(
            url=url,
            ready=False,
            status_code=None,
            duration=time.monotonic() - started,
            error=str(exc),
        )
    proc = subprocess.Popen(  # noqa: S603 — comando da receita do próprio projeto; docstring do módulo
        argv,
        cwd=str(root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # grupo próprio para teardown limpo
        text=True,
        errors="replace",
    )
    output = ""
    try:
        ready, status, error = _poll_readiness(url, ready_timeout)
    finally:
        _terminate_process_group(proc)
        try:
            if proc.stdout is not None:
                output = proc.stdout.read() or ""
        except (OSError, ValueError):
            output = ""
    return ReadinessResult(
        url=url,
        ready=ready,
        status_code=status,
        duration=time.monotonic() - started,
        error=error,
        output_tail=_tail(output),
    )


def run_verify(
    root: Path,
    recipe: Recipe,
    *,
    phases: tuple[str, ...] | list[str] | None = None,
    phase_timeout: float = DEFAULT_PHASE_TIMEOUT,
    ready_timeout: float = DEFAULT_READY_TIMEOUT,
    skip_start: bool = False,
    port_override: int | None = None,
    stop_on_failure: bool = True,
    on_output: Callable[[str], None] | None = None,
) -> VerifyResult:
    """Executa uma passada de verificação da ``recipe`` em ``root``.

    ``phases`` seleciona quais fases de comando rodam (``None`` = todas);
    uma tupla vazia pula as fases de comando. O start em background com poll
    de readiness roda quando a receita tem ``start``, a não ser que
    ``skip_start`` seja passado ou que uma fase já tenha falhado.
    """
    root = Path(root)
    selected = tuple(phases) if phases is not None else PHASE_ORDER
    result = VerifyResult(recipe_name=recipe.name)

    failed = False
    for phase in PHASE_ORDER:
        if phase not in selected:
            continue
        for command in getattr(recipe, phase):
            phase_result = _run_phase_command(phase, command, root, phase_timeout, on_output)
            result.phases.append(phase_result)
            if not phase_result.ok:
                failed = True
                if stop_on_failure:
                    return result

    if skip_start or failed or not recipe.start:
        return result

    result.readiness = _run_start_phase(recipe, root, ready_timeout, port_override)
    return result
