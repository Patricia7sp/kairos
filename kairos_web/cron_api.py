"""Authenticated schedule administration; no arbitrary shell or delivery options."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from kairos_cron.blueprints import (
    CATALOG,
    BlueprintFillError,
    blueprint_catalog_entry,
    fill_blueprint,
    get_blueprint,
)
from kairos_cron.delivery import (
    DeliveryTargetError,
    cron_delivery_targets,
    validate_delivery,
)
from kairos_cron.jobs import JobStore
from kairos_cron.lifecycle_guard import LifecycleGuardError

router = APIRouter(prefix="/api/cron")


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    prompt: StrictStr
    schedule: dict
    times: StrictInt | None = Field(default=None, ge=1, le=1_000_000)
    monitor: dict | None = None
    delivery: dict | None = None


class PauseJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


class MonitorSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = "script"
    script: StrictStr | None = None
    janela_min: StrictInt | None = Field(default=None, ge=1, le=1440)


class NotepadValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: StrictStr


class BlueprintValues(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict = Field(default_factory=dict)


def store(request):
    from kairos_web.server import _application_home

    return JobStore(_application_home(request.app))


def notepad_store(request):
    from kairos_cron.notepad import NotepadStore
    from kairos_web.server import _application_home

    return NotepadStore(_application_home(request.app))


def operate(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, "agendamento não encontrado") from exc
    except LifecycleGuardError as exc:
        raise HTTPException(422, str(exc)) from exc
    except BlueprintFillError as exc:
        raise HTTPException(422, str(exc)) from exc
    except DeliveryTargetError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, "agendamento ou arquivo de jobs inválido") from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "armazenamento de agendamentos indisponível") from exc


def delivery_adapters(request):
    """Adapters registrados no processo, se a composição os declarou.

    Sem lista fixa no código: a validação deriva do que estiver aqui (vazio
    quando nenhum adapter foi registrado — a presença dinâmica do adapter é do
    dispatcher)."""
    declared = getattr(request.app.state, "delivery_adapters", None) or ()
    return {a for a in declared if isinstance(a, str) and a and not a.isspace()}


@router.get("/delivery-targets")
def list_delivery_targets(request: Request):
    """Destinos que o dropdown deve oferecer.

    Inclui sempre o ``local`` implícito (só grava); além dele, a lista deriva
    dinamicamente dos adapters registrados — nunca de uma lista de plataformas
    hardcoded. Sem adapters registrados, só ``local`` aparece."""
    return {
        "targets": [
            {"id": "local", "name": "Local (só grava)"},
            *cron_delivery_targets(delivery_adapters(request)),
        ]
    }


@router.get("/blueprints")
def list_blueprints():
    """Catálogo de automações tipadas — cada entry reúne as quatro superfícies:
    formulário, slash command, prompt-semente e deep-link."""
    return {"blueprints": [blueprint_catalog_entry(bp) for bp in CATALOG]}


@router.get("/blueprints/{key}")
def show_blueprint(key: str):
    bp = get_blueprint(key)
    if bp is None:
        raise HTTPException(404, "blueprint não encontrado")
    return {"blueprint": blueprint_catalog_entry(bp)}


@router.post("/blueprints/{key}/jobs", status_code=201)
def create_blueprint_job(key: str, payload: BlueprintValues, request: Request):
    """Cria um job a partir de um blueprint — o mesmo executor, guard de ciclo
    de vida e validação de delivery das demais superfícies (sem segundo motor)."""
    bp = get_blueprint(key)
    if bp is None:
        raise HTTPException(404, "blueprint não encontrado")

    def _create():
        kwargs = fill_blueprint(bp, payload.values)
        if kwargs.get("delivery") is not None:
            validate_delivery(kwargs["delivery"], adapters=delivery_adapters(request))
        return store(request).create(**kwargs)

    return operate(_create)


@router.get("/jobs")
def list_jobs(request: Request):
    return {"jobs": operate(store(request).list)}


@router.post("/jobs", status_code=201)
def create_job(payload: CreateJob, request: Request):
    def _create():
        body = payload.model_dump()
        if body.get("delivery") is not None:
            validate_delivery(body["delivery"], adapters=delivery_adapters(request))
        return store(request).create(**body)

    return operate(_create)


@router.patch("/jobs/{job_id}")
def pause_job(job_id: str, payload: PauseJob, request: Request):
    return operate(lambda: store(request).set_paused(job_id, payload.paused))


@router.delete("/jobs/{job_id}")
def remove_job(job_id: str, request: Request):
    operate(lambda: store(request).remove(job_id))
    return {"removed": True}


@router.get("/jobs/{job_id}/history")
def history(job_id: str, request: Request):
    return {"executions": operate(lambda: store(request).history(job_id))}


@router.get("/jobs/{job_id}/monitor")
def get_monitor(job_id: str, request: Request):
    """Fonte configurada e último estado de verificação do job."""
    job = operate(lambda: store(request).get(job_id))
    if "monitor" not in job:
        raise HTTPException(404, "agendamento não monitorado")
    return {"monitor": job["monitor"], "monitor_state": job.get("monitor_state")}


@router.put("/jobs/{job_id}/monitor")
def set_monitor(job_id: str, payload: MonitorSet, request: Request):
    def _set():
        if payload.type == "calendar":
            return store(request).set_calendar_monitor(job_id, payload.janela_min)
        if payload.script is None:
            raise ValueError("script é obrigatório para monitor de script")
        return store(request).set_monitor(job_id, payload.script)

    return operate(_set)


@router.delete("/jobs/{job_id}/monitor")
def clear_monitor(job_id: str, request: Request):
    operate(lambda: store(request).clear_monitor(job_id))
    return {"cleared": True}


@router.post("/jobs/{job_id}/monitor/run")
async def run_monitor_source(job_id: str, request: Request):
    """Executa a fonte uma vez, sem tocar em agenda nem estado — teste do operador."""

    async def execute():
        import asyncio
        from datetime import UTC, datetime

        jobs = store(request)
        job = jobs.get(job_id)
        if "monitor" not in job:
            raise KeyError(job_id)
        if job["monitor"].get("type") == "calendar":
            from kairos_cron.calendar_monitor import check_calendar_monitor

            decision = check_calendar_monitor(
                jobs.home, job["monitor"], job.get("monitor_state"), datetime.now(UTC)
            )
            failed = decision.outcome.value == "source_error"
            return {
                "ok": not failed,
                "error": "fonte de calendário indisponível" if failed else "",
                "detail": "",
                "output_chars": 0,
                "decision": decision.outcome.value,
                "window_events": len(decision.window_events),
            }
        from kairos_cron.monitor import decide_for_source, monitor_state_from_job
        from kairos_cron.source import run_script

        result = await asyncio.to_thread(run_script, job["monitor"]["script"], home=jobs.home)
        decision = decide_for_source(monitor_state_from_job(job), result)
        return {
            "ok": result.ok,
            "error": result.error or "",
            "detail": result.detail or "",
            "output_chars": len(result.output) if result.output else 0,
            "decision": decision.outcome.value,
        }

    return await operate(execute)


@router.get("/jobs/{job_id}/notepad")
def notepad_list(job_id: str, request: Request):
    return {"job_id": job_id, "notes": operate(lambda: notepad_store(request).list(job_id))}


@router.get("/jobs/{job_id}/notepad/{key}")
def notepad_get(job_id: str, key: str, request: Request):
    return operate(
        lambda: {"job_id": job_id, "key": key, "value": notepad_store(request).get(job_id, key)}
    )


@router.put("/jobs/{job_id}/notepad/{key}")
def notepad_set(job_id: str, key: str, payload: NotepadValue, request: Request):
    return operate(lambda: notepad_store(request).set(job_id, key, payload.value))


@router.delete("/jobs/{job_id}/notepad/{key}")
def notepad_delete(job_id: str, key: str, request: Request):
    store = notepad_store(request)
    return {
        "job_id": job_id,
        "key": key,
        "deleted": operate(lambda: store.delete(job_id, key)),
    }


@router.delete("/jobs/{job_id}/notepad")
def notepad_clear(job_id: str, request: Request):
    return {
        "job_id": job_id,
        "cleared": operate(lambda: notepad_store(request).clear(job_id)),
    }


@router.get("/status")
def status(request: Request):
    from kairos_cron.schedule import croniter_available

    scheduler = getattr(request.app.state, "cron_scheduler", None)
    task = getattr(request.app.state, "cron_task", None)
    return {
        "running": task is not None and not task.done(),
        "last_tick": scheduler.last_tick if scheduler else None,
        "error": scheduler.last_error if scheduler else None,
        "interval_seconds": 60,
        "croniter": croniter_available(),
    }
