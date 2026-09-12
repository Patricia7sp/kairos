"""Authenticated schedule administration; no arbitrary shell or delivery options."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from kairos_cron.jobs import JobStore

router = APIRouter(prefix="/api/cron")


class CreateJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    prompt: StrictStr
    schedule: dict
    times: StrictInt | None = Field(default=None, ge=1, le=1_000_000)


class PauseJob(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paused: StrictBool


def store(request):
    from kairos_web.server import _application_home

    return JobStore(_application_home(request.app))


def operate(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, "agendamento não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(422, "agendamento ou arquivo de jobs inválido") from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(503, "armazenamento de agendamentos indisponível") from exc


@router.get("/jobs")
def list_jobs(request: Request):
    return {"jobs": operate(store(request).list)}


@router.post("/jobs", status_code=201)
def create_job(payload: CreateJob, request: Request):
    return operate(lambda: store(request).create(**payload.model_dump()))


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
