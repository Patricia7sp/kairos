"""Operational schedule commands sharing Web's store and executor."""

from __future__ import annotations

import asyncio

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler


def command(  # noqa: PLR0912 - subcomandos de cron são cascata de dispatch; mantidos num handler
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
        monitor = (
            {"type": "script", "script": args.monitor} if getattr(args, "monitor", None) else None
        )
        result = store.create(
            name=args.name, prompt=args.prompt, schedule=schedule, times=args.times, monitor=monitor
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
    elif sub == "monitor-clear":
        result = store.clear_monitor(args.job_id)
    elif sub == "monitor-show":
        result = show_monitor(store, args.job_id)
    elif sub == "monitor-run":
        result = run_monitor_now(store, args.job_id, home)
    elif sub == "notepad":
        result = cmd_notepad(args, home)
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

    Usa `JobStore` apenas para ler o comando; a execução é isolada (ver
    `kairos_cron.source`). Resultado conta como teste da fonte, não como tick.
    """
    from kairos_cron.monitor import decide_for_source, monitor_state_from_job

    job = store.get(job_id)
    if "monitor" not in job:
        raise KeyError("agendamento não monitorado")
    import asyncio

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
