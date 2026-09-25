"""Comando `kairos verify` — verificação pós-edição de código.

Origem no legado: `hermes_cli/verify_cmd.py` + `agent/verify/` (port scoped
de superagent-ai/grok-cli). A receita do projeto é detectada estaticamente ou
carregada do manifesto `.kairos/environment.json`, as fases
bootstrap/build/test rodam no checkout e um start em background é provado
por readiness de porta. A evidência é o relatório (humano ou `--json`) e o
exit code — sem ledger de evidência (D-CLI.10).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from kairos_cli.handlers import ExitCode
from kairos_cli.verify_recipe import load_or_detect, manifest_path, save_manifest
from kairos_cli.verify_runner import DEFAULT_PHASE_TIMEOUT, DEFAULT_READY_TIMEOUT


def _phase_timeout(args) -> float:
    value = getattr(args, "timeout", None)
    if value is None or value <= 0:
        return DEFAULT_PHASE_TIMEOUT
    return value


def _ready_timeout(args) -> float:
    value = getattr(args, "ready_timeout", None)
    if value is None or value <= 0:
        return DEFAULT_READY_TIMEOUT
    return value


def run_verify(args) -> int:
    root = Path(getattr(args, "path", None) or ".").resolve()
    if not root.is_dir():
        print(f"erro: não é um diretório: {root}", file=sys.stderr)
        return ExitCode.USAGE

    recipe, source = load_or_detect(root)
    if recipe is None:
        message = (
            f"Nenhum projeto reconhecível em {root}.\n"
            f"Crie {manifest_path(root)} para definir uma receita manualmente."
        )
        if getattr(args, "json", False):
            print(json.dumps({"ok": False, "error": "no-recipe", "root": str(root)}))
        else:
            print(message, file=sys.stderr)
        return ExitCode.ERROR

    if getattr(args, "port", None):
        recipe.port = args.port

    if getattr(args, "save", False):
        path = save_manifest(root, recipe)
        if not getattr(args, "json", False):
            print(f"Manifesto salvo: {path}")

    if getattr(args, "detect_only", False):
        payload = {"source": source, "recipe": recipe.to_dict()}
        print(json.dumps(payload, indent=None if getattr(args, "json", False) else 2))
        return ExitCode.OK

    phases = None
    if getattr(args, "phase", None):
        phases = tuple(args.phase)

    from kairos_cli.verify_runner import run_verify as run_verify_phases

    result = run_verify_phases(
        root,
        recipe,
        phases=phases,
        phase_timeout=_phase_timeout(args),
        ready_timeout=_ready_timeout(args),
        skip_start=getattr(args, "skip_start", False),
        port_override=getattr(args, "port", None),
    )

    if getattr(args, "json", False):
        payload = result.to_dict()
        payload["source"] = source
        print(json.dumps(payload, ensure_ascii=False))
        return ExitCode.OK if result.ok else ExitCode.ERROR

    _print_human_report(recipe, source, result)
    return ExitCode.OK if result.ok else ExitCode.ERROR


def _print_human_report(recipe, source: str, result) -> None:
    origem = "manifest" if source == "manifest" else "detecção"
    print(f"Receita: {recipe.name} ({recipe.kind}) — origem: {origem}")
    print()
    if result.phases:
        width = max(len(p.phase) for p in result.phases)
        for p in result.phases:
            if p.unsupported:
                status = "RECUSADO"
            elif p.timed_out:
                status = "TIMEOUT"
            else:
                status = "PASS" if p.ok else "FALHA"
            print(f"  {p.phase.ljust(width)}  {status:<8}  {p.duration:6.1f}s  {p.command}")
    else:
        print("  (nenhuma fase executada)")

    if result.readiness is not None:
        r = result.readiness
        status = (
            f"ready (HTTP {r.status_code})" if r.ready else f"não ready ({r.error or 'timeout'})"
        )
        rotulo = "readiness"
        largura = max(len(rotulo), max((len(p.phase) for p in result.phases), default=len(rotulo)))
        print(
            f"  {rotulo.ljust(largura)}  {'PASS' if r.ready else 'FALHA':<7}  "
            f"{r.duration:6.1f}s  {recipe.start}"
        )
        print()
        print(f"Readiness: {r.url} -> {status}")

    print()
    print(f"Resultado: {'OK' if result.ok else 'FALHOU'}")
    for p in result.phases:
        if p.ok:
            continue
        print(f"\n--- {p.phase} ({p.command}) ---")
        if p.unsupported:
            print(p.unsupported)
        else:
            print(p.output_tail.rstrip())
    if result.readiness is not None and not result.readiness.ready and result.readiness.output_tail:
        print("\n--- cauda da saída: start ---")
        print(result.readiness.output_tail.rstrip())
