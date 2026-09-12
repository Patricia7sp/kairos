"""Read-only operational events; no arbitrary filesystem or environment access."""

from typing import Literal

from fastapi import APIRouter, Query, Request

from kairos_observability.service_events import read_service_events

router = APIRouter(prefix="/api")


@router.get("/logs")
def get_logs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    service: Literal["web", "chat", "search", "cron"] | None = None,
    level: Literal["info", "warning", "error"] | None = None,
):
    from kairos_web.server import _application_home

    return read_service_events(
        _application_home(request.app),
        limit=limit,
        service=service,
        level=level,
    )
