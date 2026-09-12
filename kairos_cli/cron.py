"""Operational schedule commands sharing Web's store and executor."""

from __future__ import annotations

import asyncio

from kairos_cron.jobs import JobStore
from kairos_cron.scheduler import Scheduler


def command(args, home):
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
        result = store.create(name=args.name, prompt=args.prompt, schedule=schedule)
    elif sub == "list":
        result = {"jobs": store.list()}
    elif sub in ("pause", "resume"):
        result = store.set_paused(args.job_id, sub == "pause")
    elif sub == "remove":
        store.remove(args.job_id)
        result = {"removed": True}
    elif sub == "history":
        result = {"executions": store.history(args.job_id)}
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
            "croniter": croniter_available(),
            "ticker": "observado pela API Web; CLI executa um tick por chamada",
        }
    _emit(result, as_json=args.json)
    return 1 if sub == "tick" and (result["failed"] or result["busy"]) else 0
