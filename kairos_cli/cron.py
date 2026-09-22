"""Operational schedule commands sharing Web's store and executor."""

from __future__ import annotations

import asyncio

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler


def command(  # noqa: PLR0912, PLR0915 - subcomandos de cron são cascata de dispatch; mantidos num handler
    args, home
):
    from kairos_cli.handlers import _emit

    store = JobStore(home)
    sub = getattr(args, "cron_command", None) or ("tick" if args.command == "tick" else "status")
    if sub == "create":
        if args.at:
            schedule = {"kind": "once", "run_at": args.at}
        elif args.every:
            schedule = {"kind": "interval", "minutes": args.every}
        else:
            schedule = {"kind": "cron", "expr": args.expr}
        if getattr(args, "window_minutes", None) is not None and not getattr(
            args, "monitor_calendar", False
        ):
            raise ValueError("--window-minutes só faz sentido com --monitor-calendar")
        if getattr(args, "monitor_calendar", False):
            monitor = {
                "type": "calendar",
                "janela_min": getattr(args, "window_minutes", None),
            }
        elif getattr(args, "monitor", None):
            monitor = {"type": "script", "script": args.monitor}
        else:
            monitor = None
        delivery = {"target": args.deliver} if getattr(args, "deliver", None) else None
        result = store.create(
            name=args.name,
            prompt=args.prompt,
            schedule=schedule,
            times=args.times,
            monitor=monitor,
            delivery=delivery,
        )
    elif sub == "list":
        result = {"jobs": store.list()}
    elif sub in ("pause", "resume"):
        result = store.set_paused(args.job_id, sub == "pause")
    elif sub == "remove":
        store.remove(args.job_id)
        result = {"removed": True}
    elif sub == "history":
        result = {"executions": store.history(args.job_id)}
    elif sub == "monitor-set":
        result = store.set_monitor(args.job_id, args.script)
    elif sub == "monitor-calendar-set":
        result = store.set_calendar_monitor(args.job_id, getattr(args, "window_minutes", None))
    elif sub == "monitor-clear":
        result = store.clear_monitor(args.job_id)
    elif sub == "monitor-show":
        result = show_monitor(store, args.job_id)
    elif sub == "monitor-run":
        result = run_monitor_now(store, args.job_id, home)
    elif sub == "notepad":
        result = cmd_notepad(args, home)
    elif sub == "blueprint":
        result = cmd_blueprint(args, home, store)
    elif sub == "tick":

        async def run():
            from kairos_integration.composition import build_interaction_service

            async with build_interaction_service(home) as service:
                return await Scheduler(home, service).tick()

        result = asyncio.run(run())
    else:
        from kairos_cron.schedule import croniter_available

        jobs = store.list()
        result = {
            "jobs": len(jobs),
            "enabled": sum(j["enabled"] and not j["paused"] for j in jobs),
            "monitored": sum("monitor" in j for j in jobs),
            "croniter": croniter_available(),
            "ticker": "observado pela API Web; CLI executa um tick por chamada",
        }
    _emit(result, as_json=args.json)
    return 1 if sub == "tick" and (result["failed"] or result["busy"]) else 0


def cmd_notepad(args, home) -> dict:
    """Memória KV durável de um job (não exige que o job ainda exista — o
    bloco é chaveado por id, como no legado)."""
    from kairos_cron.notepad import NotepadStore

    store = NotepadStore(home)
    action = getattr(args, "notepad_action", "list") or "list"
    job_id, key, value = args.job_id, getattr(args, "key", None), getattr(args, "value", None)
    if action == "set":
        if not key or value is None:
            raise ValueError("set exige chave e valor")
        return store.set(job_id, key, value)
    if action == "get":
        if not key:
            raise ValueError("get exige chave")
        return {"job_id": job_id, "key": key, "value": store.get(job_id, key)}
    if action == "delete":
        if not key:
            raise ValueError("delete exige chave")
        return {"job_id": job_id, "key": key, "deleted": store.delete(job_id, key)}
    return {"job_id": job_id, "notes": store.list(job_id)}


def cmd_blueprint(args, home, store) -> dict:
    """`kairos cron blueprint list|show|create` — catálogo tipado sem segundo
    motor de jobs: o create preenche o blueprint e reusa o mesmo `JobStore`."""
    from kairos_cron.blueprints import (
        CATALOG,
        blueprint_catalog_entry,
        fill_blueprint,
        get_blueprint,
        parse_blueprint_slash,
    )

    action = getattr(args, "blueprint_command", "list") or "list"
    if action == "list":
        entries = [blueprint_catalog_entry(bp) for bp in CATALOG]
        category = getattr(args, "category", None)
        if category:
            entries = [e for e in entries if e["category"] == category]
        return {"blueprints": entries}

    if action == "show":
        bp = get_blueprint(args.key)
        if bp is None:
            raise KeyError("blueprint não encontrado")
        return blueprint_catalog_entry(bp)

    # create
    key = args.key.strip()
    values = {}

    def apply_pairs(pairs):
        for raw in pairs:
            name, _, value = raw.partition("=")
            if not name:
                raise ValueError(f"par de slot inválido: {raw!r} (esperado slot=valor)")
            values[name] = value

    if key.startswith("/blueprint"):
        key, slash_values = parse_blueprint_slash(key)
        values.update(slash_values)
    apply_pairs(args.sets or ())
    bp = get_blueprint(key)
    if bp is None:
        raise KeyError("blueprint não encontrado")
    return store.create(**fill_blueprint(bp, values))


def show_monitor(store, job_id: str) -> dict:
    job = store.get(job_id)
    if "monitor" not in job:
        raise KeyError("agendamento não monitorado")
    monitor = dict(job["monitor"])
    state = job.get("monitor_state") or {}
    monitor.update(
        {
            "ultima_verificacao": state.get("last_checked_at"),
            "ultima_mudanca": state.get("last_changed_at"),
            "agenda_proxima": job.get("next_run_at"),
        }
    )
    return monitor


def run_monitor_now(store, job_id: str, home):
    """Executa a fonte de um monitor uma vez, sem tocar em agenda nem estado.

    Usa `JobStore` apenas para ler a configuração; a execução é isolada (ver
    `kairos_cron.source`). Para o monitor de calendário, a leitura final (decisão
    de janela) é o teste da fonte, sem persistir nada. Resultado conta como
    teste da fonte, não como tick.
    """
    from datetime import UTC, datetime

    job = store.get(job_id)
    if "monitor" not in job:
        raise KeyError("agendamento não monitorado")

    if job["monitor"].get("type") == "calendar":
        from kairos_cron.calendar_monitor import check_calendar_monitor

        decision = check_calendar_monitor(
            home, job["monitor"], job.get("monitor_state"), datetime.now(UTC)
        )
        return {
            "ok": decision.outcome.value != "source_error",
            "erro": ""
            if decision.outcome.value != "source_error"
            else "fonte de calendário indisponível",
            "detalhe": "",
            "bytes_saida": 0,
            "decisao": decision.outcome.value,
            "eventos_na_janela": len(decision.window_events),
        }

    import asyncio

    from kairos_cron.monitor import decide_for_source, monitor_state_from_job
    from kairos_cron.source import run_script

    def source_result():
        return run_script(job["monitor"]["script"], home=home)

    result = asyncio.run(asyncio.to_thread(source_result))
    decision = decide_for_source(monitor_state_from_job(job), result)
    return {
        "ok": result.ok,
        "erro": result.error,
        "detalhe": result.detail,
        "bytes_saida": len(result.output or ""),
        "decisao": decision.outcome.value,
    }
