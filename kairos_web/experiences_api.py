"""API do painel para o aprendizado operacional (`kairos_memory`).

Fecha a divergência #2 do diagnóstico: as experiências eram geridas só pela
CLI e expostas (opt-in) no terminal e no Telegram; a web não tinha tela. Aqui
a web lista, cria e revisa as mesmas experiências do `ExperienceStore`.

Sem efeito fingido: confirmar, invalidar e registrar resultado operam o store
real e devolvem o registro atualizado. ID inexistente é 404; transição em
estado errado é 409 — nunca sucesso silencioso.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr

from kairos_memory import Experience, ExperienceStatus, ExperienceStore

router = APIRouter(prefix="/api")


def _home(request: Request) -> Path:
    from kairos_web.server import _application_home

    return Path(_application_home(request.app))


def _store(request: Request) -> ExperienceStore:
    return ExperienceStore(_home(request))


def _serialize(exp: Experience) -> dict:
    return {
        "id": exp.id,
        "status": exp.status.value,
        "trigger": exp.trigger,
        "observation": exp.observation,
        "correction": exp.correction,
        "scope": exp.scope,
        "source": exp.source,
        "confidence": exp.confidence,
        "hits": exp.hits,
        "successes": exp.successes,
        "created_at": exp.created_at,
        "updated_at": exp.updated_at,
        "version": exp.version,
    }


def _status_filter(status: str | None, include_all: bool) -> tuple[ExperienceStatus, ...] | None:
    if include_all:
        return None
    if not status:
        return (ExperienceStatus.ATIVA, ExperienceStatus.CANDIDATA)
    wanted: list[ExperienceStatus] = []
    for bruto in status.split(","):
        token = bruto.strip()
        if not token:
            continue
        try:
            wanted.append(ExperienceStatus(token))
        except ValueError as exc:
            raise HTTPException(422, f"status inválido: {token}") from exc
    return tuple(wanted) or (ExperienceStatus.ATIVA, ExperienceStatus.CANDIDATA)


class ExperienceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: StrictStr
    correction: StrictStr
    observation: StrictStr = ""
    scope: StrictStr = "global"
    confidence: float = Field(default=0.6, ge=0.05, le=0.99)
    confirm: StrictBool = False


@router.get("/experiences")
def list_experiences(
    request: Request,
    status: str | None = None,
    scope: str | None = None,
    include_all: bool = False,
    limit: int = 50,
):
    """Lista as experiências; por padrão ativas + candidatas, como a CLI."""
    store = _store(request)
    wanted = _status_filter(status, include_all)
    items = store.list(status=wanted, scope=scope, limit=max(1, min(limit, 500)))
    todas = store.list(status=None, limit=10_000)
    counts = {estado.value: 0 for estado in ExperienceStatus}
    for exp in todas:
        counts[exp.status.value] += 1
    counts["total"] = len(todas)
    return {"items": [_serialize(exp) for exp in items], "counts": counts}


@router.post("/experiences")
def create_experience(body: ExperienceBody, request: Request):
    """Cria uma experiência; só vira ativa se `confirm` for true (revisão humana)."""
    trigger = body.trigger.strip()
    correction = body.correction.strip()
    if not trigger or not correction:
        raise HTTPException(422, "trigger e correction são obrigatórios")
    status = ExperienceStatus.ATIVA if body.confirm else ExperienceStatus.CANDIDATA
    exp = _store(request).add(
        trigger=trigger,
        observation=body.observation,
        correction=correction,
        source="usuario",
        scope=body.scope.strip() or "global",
        confidence=body.confidence,
        status=status,
    )
    return _serialize(exp)


def _require(store: ExperienceStore, exp_id: str) -> Experience:
    exp = store.get(exp_id)
    if exp is None:
        raise HTTPException(404, "experiência não encontrada")
    return exp


@router.post("/experiences/{exp_id}/confirm")
def confirm_experience(exp_id: str, request: Request):
    store = _store(request)
    exp = _require(store, exp_id)
    if exp.status != ExperienceStatus.CANDIDATA:
        raise HTTPException(409, f"não é possível confirmar uma experiência {exp.status.value}")
    result = store.confirm(exp_id)
    if result is None:  # corrida: mudou entre o get e o confirm
        raise HTTPException(409, "estado da experiência mudou; recarregue")
    return _serialize(result)


@router.post("/experiences/{exp_id}/invalidate")
def invalidate_experience(exp_id: str, request: Request):
    store = _store(request)
    exp = _require(store, exp_id)
    if exp.status == ExperienceStatus.INVALIDA:
        raise HTTPException(409, "experiência já está inválida")
    result = store.invalidate(exp_id)
    if result is None:
        raise HTTPException(409, "estado da experiência mudou; recarregue")
    return _serialize(result)


@router.delete("/experiences/{exp_id}")
def reject_experience(exp_id: str, request: Request):
    """Rejeita e remove o registro — o mesmo efeito do `reject` da CLI."""
    store = _store(request)
    _require(store, exp_id)
    if not store.reject(exp_id):
        raise HTTPException(409, "estado da experiência mudou; recarregue")
    return {"removed": True, "id": exp_id}


class RecordBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success: StrictBool


@router.post("/experiences/{exp_id}/record")
def record_outcome(exp_id: str, body: RecordBody, request: Request):
    """Registra o desfecho de um uso: sobe/baixa a confiança e pode auto-invalidar."""
    store = _store(request)
    _require(store, exp_id)
    result = store.record_outcome(exp_id, success=body.success)
    if result is None:
        raise HTTPException(404, "experiência não encontrada")
    return _serialize(result)
