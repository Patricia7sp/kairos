"""Monitores de fonte nos agendamentos — mesma store e execução do cron."""

from __future__ import annotations

from kairos_cron.jobs import JobStore


def command(args, home):
    from kairos_cli.handlers import _emit

    store = JobStore(home)
    sub = getattr(args, "monitoring_command", None) or "status"
    if sub == "list":
        result = {
            "monitored": [
                {
                    "id": job["id"],
                    "name": job["name"],
                    "script": job.get("monitor", {}).get("script"),
                    "proxima": job.get("next_run_at"),
                }
                for job in store.list()
                if "monitor" in job
            ]
        }
    elif sub == "status":
        result = {
            "jobs": len(store.list()),
            "monitored": sum("monitor" in job for job in store.list()),
            "com_ultima_verificacao": sum(
                bool(job.get("monitor_state", {}).get("last_checked_at")) for job in store.list()
            ),
        }
    elif sub == "test":
        from kairos_cli.cron import run_monitor_now

        result = run_monitor_now(store, args.job_id, home)
    else:
        result = {"erro": f"subcomando desconhecido: {sub}"}
    _emit(result, as_json=args.json)
    return 0
