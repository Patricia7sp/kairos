"""Comando `kairos memory` — memória de longo prazo (toolset `memory`).

`status`/`off` operam o toolset `memory` (MEMORY.md/USER.md). O subcomando
`experiences` opera o aprendizado operacional (`kairos_memory`): listar, gravar
e revisar experiências. A separação é deliberada — o toolset `memory` é a
memória do agente; `kairos_memory` guarda correções validadas por humano.
"""

from pathlib import Path

from kairos_memory import ExperienceStatus, ExperienceStore
from kairos_tools.memory import MemoryStore


async def run_memory(home: Path, args) -> int:
    """Executor real do toolset `memory`."""
    subcommand = getattr(args, "memory_command", None) or getattr(args, "subcommand", None)
    if subcommand == "experiences":
        return _run_experiences(home, args)

    store = MemoryStore(home=home)
    if subcommand == "off":
        store.clear()
        print(
            "Memória de longo prazo limpa "
            f"(removidos {store.mem_dir / 'MEMORY.md'} e {store.mem_dir / 'USER.md'})."
        )
    elif subcommand == "status":
        for target in ("memory", "user"):
            result = store.list(target)
            if not result.get("success"):
                print(f"[{target}] {result.get('error', 'erro de leitura')}")
                return 1
            entries = result.get("entries", [])
            print(f"{target.upper()}: {result.get('usage')} — {len(entries)} entrada(s)")
            if entries:
                for i, entry in enumerate(entries, 1):
                    print(f"  {i}. {entry}")
    else:
        print("Subcomando inválido. Use: memory off | memory status | memory experiences")
        return 1
    return 0


def _run_experiences(home: Path, args) -> int:
    store = ExperienceStore(home)
    action = getattr(args, "experience_action", None) or ""
    handlers = {
        "list": lambda a: _exp_list(store, a),
        "add": lambda a: _exp_add(store, a),
        "confirm": lambda a: _exp_confirm(store, a),
        "reject": lambda a: _exp_reject(store, a),
        "invalidate": lambda a: _exp_invalidate(store, a),
        "record": lambda a: _exp_record(store, a),
    }
    handler = handlers.get(action)
    if handler is None:
        print(f"Ação desconhecida: {action}")
        return 1
    return handler(args)


def _exp_list(store, args) -> int:
    include_all = getattr(args, "all", False)
    statuses = None if include_all else (ExperienceStatus.ATIVA, ExperienceStatus.CANDIDATA)
    items = store.list(status=statuses)
    if not items:
        print("Nenhuma experiência registrada.")
        return 0
    for exp in items:
        print(
            f"[{exp.id}] {exp.status.value} conf:{exp.confidence:.2f} "
            f"hits:{exp.hits} ({exp.successes} ok) scope:{exp.scope}"
        )
        print(f"    gatilho: {exp.trigger}")
        print(f"    correção: {exp.correction}")
    return 0


def _exp_add(store, args) -> int:
    trigger = (getattr(args, "trigger", None) or "").strip()
    correction = (getattr(args, "correction", None) or "").strip()
    if not trigger or not correction:
        print("Erro: --trigger e --correction são obrigatórios em add.")
        return 1
    try:
        status = ExperienceStatus(getattr(args, "status", "ativa"))
    except ValueError:
        print("Erro: --status deve ser candidata, ativa ou invalida.")
        return 1
    exp = store.add(
        trigger=trigger,
        observation=getattr(args, "observation", "") or "",
        correction=correction,
        source="usuario",
        scope=getattr(args, "scope", "global") or "global",
        confidence=getattr(args, "confidence", 0.9),
        status=status,
    )
    print(f"Experiência registrada: [{exp.id}] {exp.status.value}")
    return 0


def _require_id(args) -> str | None:
    exp_id = getattr(args, "experience_id", None)
    if exp_id is None:
        print(f"Erro: a ação '{getattr(args, 'experience_action', '?')}' exige um ID.")
        return None
    return exp_id


def _exp_confirm(store, args) -> int:
    exp_id = _require_id(args)
    if exp_id is None:
        return 1
    result = store.confirm(exp_id)
    if result is None:
        print(f"Experiência não encontrada ou já ativa: {exp_id}")
        return 1
    print(f"Experiência confirmada: [{result.id}] {result.status.value}")
    return 0


def _exp_reject(store, args) -> int:
    exp_id = _require_id(args)
    if exp_id is None:
        return 1
    if not store.reject(exp_id):
        print(f"Experiência não encontrada: {exp_id}")
        return 1
    print(f"Experiência rejeitada e removida: {exp_id}")
    return 0


def _exp_invalidate(store, args) -> int:
    exp_id = _require_id(args)
    if exp_id is None:
        return 1
    result = store.invalidate(exp_id)
    if result is None:
        print(f"Experiência não encontrada ou já inválida: {exp_id}")
        return 1
    print(f"Experiência invalidada: [{result.id}] {result.status.value}")
    return 0


def _exp_record(store, args) -> int:
    exp_id = _require_id(args)
    if exp_id is None:
        return 1
    success = bool(getattr(args, "success", False))
    result = store.record_outcome(exp_id, success=success)
    if result is None:
        print(f"Experiência não encontrada: {exp_id}")
        return 1
    print(
        f"Resultado registrado: [{result.id}] "
        f"{'sucesso' if success else 'falha'} -> conf:{result.confidence:.2f} "
        f"status:{result.status.value}"
    )
    return 0
