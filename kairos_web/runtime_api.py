"""Authenticated REST commands for the shared Agent Runtime host."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from kairos_runtime import RuntimeErrorInfo, public_error
from kairos_state import connect, initialize_schema

__all__ = ["router"]

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LoginRequest(_StrictModel):
    method: Literal["apiKey", "chatgpt", "chatgptDeviceCode"]
    api_key: str | None = None

    @model_validator(mode="after")
    def validate_secret_source(self):
        if self.method == "apiKey" and (not self.api_key or not self.api_key.strip()):
            raise ValueError("api_key é obrigatória para apiKey")
        if self.method != "apiKey" and self.api_key is not None:
            raise ValueError("api_key não pertence a este método")
        return self


class LoginCancelRequest(_StrictModel):
    login_id: str

    @field_validator("login_id")
    @classmethod
    def nonblank_login(cls, value: str) -> str:
        return _nonblank(value, "login_id")


class RuntimeSessionCreateRequest(_StrictModel):
    cwd: str
    sandbox: Literal["read_only", "workspace_write", "broad_access"]
    consent: bool = False
    parent_session_id: str | None = None
    session_id: str | None = None

    @field_validator("cwd")
    @classmethod
    def nonblank_cwd(cls, value: str) -> str:
        return _nonblank(value, "cwd")

    @field_validator("parent_session_id", "session_id")
    @classmethod
    def optional_nonblank(cls, value: str | None, info):
        return None if value is None else _nonblank(value, info.field_name)


class RuntimeTurnRequest(_StrictModel):
    content: str
    idempotency_key: str

    @field_validator("content", "idempotency_key")
    @classmethod
    def nonblank_turn(cls, value: str, info):
        return _nonblank(value, info.field_name)


class RuntimeCancelRequest(_StrictModel):
    turn_id: str

    @field_validator("turn_id")
    @classmethod
    def nonblank_turn_id(cls, value: str) -> str:
        return _nonblank(value, "turn_id")


class RuntimeApprovalRequest(_StrictModel):
    decision: Literal["accept", "decline"]


class ProjectVersionResponse(_StrictModel):
    schema_version: Literal[1]
    revision: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    name: str
    baseline_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    cwd: str


class RuntimeStatusResponse(_StrictModel):
    enabled: bool
    state: Literal["disabled", "unavailable", "ready"]
    authorized_projects: list[str]
    project_versions: list[ProjectVersionResponse] = Field(default_factory=list)
    sandbox_profiles: list[Literal["read_only", "workspace_write", "broad_access"]]

    @field_validator("authorized_projects")
    @classmethod
    def nonblank_projects(cls, value: list[str]) -> list[str]:
        if any(not project.strip() for project in value):
            raise ValueError("authorized_projects contém caminho vazio")
        return value


class AccountStatusResponse(_StrictModel):
    requires_openai_auth: bool
    authenticated: bool
    auth_mode: Literal["apiKey", "chatgpt"] | None
    email: str | None
    plan_type: str | None
    login_state: Literal["idle", "starting", "pending", "succeeded", "failed", "cancelled"]


class ApiKeyLoginResponse(_StrictModel):
    mode: Literal["apiKey"]
    state: Literal["succeeded"]


class ChatGptLoginResponse(_StrictModel):
    mode: Literal["chatgpt"]
    state: Literal["pending"]
    login_id: str
    auth_url: str


class DeviceCodeLoginResponse(_StrictModel):
    mode: Literal["chatgptDeviceCode"]
    state: Literal["pending"]
    login_id: str
    user_code: str
    verification_url: str


_LOGIN_RESPONSE = TypeAdapter(ApiKeyLoginResponse | ChatGptLoginResponse | DeviceCodeLoginResponse)


def _nonblank(value: str, field: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} é obrigatório")
    return value.strip()


def _client(request: Request):
    client = getattr(request.app.state, "runtime_client", None)
    if client is None:
        service = getattr(request.app.state, "interaction_service", None)
        client = getattr(service, "runtime_client", None)
    if client is None:
        raise RuntimeErrorInfo("unavailable", "runtime indisponível", True)
    return client


def _home(request: Request) -> Path:
    supplied = getattr(request.app.state, "kairos_home", None)
    return (
        Path(supplied)
        if supplied is not None
        else Path(os.environ.get("KAIROS_HOME", Path.home() / ".kairos"))
    )


def _require_runtime_session(request: Request, session_id: str) -> JSONResponse | None:
    db = connect(_home(request) / "state.db")
    try:
        initialize_schema(db)
        row = db.execute("SELECT execution_kind FROM sessions WHERE id=?", (session_id,)).fetchone()
    finally:
        db.close()
    if row is None:
        return JSONResponse({"error": "session_not_found", "id": session_id}, status_code=404)
    if row["execution_kind"] != "agent_runtime":
        return JSONResponse(
            {"error": "session_identity_conflict", "id": session_id}, status_code=409
        )
    return None


def _runtime_error(exc: RuntimeErrorInfo) -> JSONResponse:
    safe = public_error(exc.code)
    if exc.code in {
        "unavailable",
        "transport",
        "runtime_internal",
        "incompatible",
        "thread_missing",
    }:
        status = 503
    elif exc.code in {"invalid_directory", "invalid_policy", "invalid_event", "sequence_gap"}:
        status = 422
    else:
        status = 409
    return JSONResponse(
        {"error": safe["code"], "message": safe["message"], "retryable": safe["retryable"]},
        status_code=status,
    )


async def _call(request: Request, method: str, *args, **kwargs):
    try:
        return await getattr(_client(request), method)(*args, **kwargs)
    except RuntimeErrorInfo as exc:
        return _runtime_error(exc)


def _validated_response(value, schema):
    if isinstance(value, JSONResponse):
        return value
    try:
        parsed = (
            schema.validate_python(value)
            if isinstance(schema, TypeAdapter)
            else schema.model_validate(value)
        )
    except ValidationError:
        return _runtime_error(
            RuntimeErrorInfo("invalid_event", "resposta de runtime inválida", False)
        )
    return parsed.model_dump()


def _session_response(value):
    if isinstance(value, JSONResponse):
        return value
    if not isinstance(value, dict) or not isinstance(value.get("session_id"), str):
        return _runtime_error(
            RuntimeErrorInfo("invalid_event", "resposta de runtime inválida", False)
        )
    return {
        "session_id": value["session_id"],
        "execution_kind": "agent_runtime",
        "runtime_kind": value.get("runtime_kind", "codex"),
        "cwd": value.get("requested_cwd", value.get("canonical_cwd")),
        "canonical_cwd": value.get("canonical_cwd"),
        "sandbox": value.get("sandbox_profile"),
        "state": value.get("state"),
        "external_thread_id": value.get("external_thread_id"),
        "protocol_version": value.get("protocol_version"),
        "capabilities": value.get("capabilities"),
        "parent_session_id": value.get("parent_session_id"),
        "broad_consent": value.get("broad_consent_at") is not None,
    }


@router.get("/status")
async def runtime_status(request: Request):
    return _validated_response(await _call(request, "status"), RuntimeStatusResponse)


@router.get("/account")
async def runtime_account(request: Request):
    return _validated_response(await _call(request, "account_status"), AccountStatusResponse)


@router.post("/login")
async def runtime_login(payload: LoginRequest, request: Request):
    return _validated_response(
        await _call(request, "account_login", payload.method, payload.api_key), _LOGIN_RESPONSE
    )


@router.post("/login/cancel")
async def runtime_login_cancel(payload: LoginCancelRequest, request: Request):
    result = await _call(request, "account_cancel", payload.login_id)
    return (
        result
        if isinstance(result, JSONResponse)
        else {"login_id": payload.login_id, "status": "cancelled"}
    )


@router.post("/logout")
async def runtime_logout(request: Request):
    result = await _call(request, "account_logout")
    return result if isinstance(result, JSONResponse) else {"status": "logged_out"}


@router.post("/sessions")
async def runtime_session_create(payload: RuntimeSessionCreateRequest, request: Request):
    return _session_response(
        await _call(
            request,
            "create",
            payload.cwd,
            payload.sandbox,
            consent=payload.consent,
            source="web",
            parent_session_id=payload.parent_session_id,
            session_id=payload.session_id,
        )
    )


@router.get("/sessions/{session_id}/changes")
async def runtime_session_changes(session_id: str, request: Request):
    if failure := _require_runtime_session(request, session_id):
        return failure
    result = await _call(request, "changes", session_id)
    if isinstance(result, JSONResponse):
        return result
    from kairos_runtime.reviews import validate_review

    try:
        result = await asyncio.to_thread(validate_review, result)
        if result["session_id"] != session_id:
            raise ValueError("review session mismatch")
    except (ValueError, TypeError):
        return _runtime_error(RuntimeErrorInfo("invalid_event", "revisão inválida", False))
    return result


@router.post("/sessions/{session_id}/end")
async def runtime_session_end(session_id: str, request: Request):
    if failure := _require_runtime_session(request, session_id):
        return failure
    result = await _call(request, "end", session_id)
    return (
        result
        if isinstance(result, JSONResponse)
        else {"session_id": session_id, "status": "ended"}
    )


@router.post("/sessions/{session_id}/turns", status_code=202)
async def runtime_turn_submit(session_id: str, payload: RuntimeTurnRequest, request: Request):
    if failure := _require_runtime_session(request, session_id):
        return failure
    result = await _call(request, "submit", session_id, payload.content, payload.idempotency_key)
    if isinstance(result, JSONResponse):
        return result
    return {"session_id": session_id, "turn_id": result, "status": "accepted"}


@router.post("/sessions/{session_id}/cancel")
async def runtime_turn_cancel(session_id: str, payload: RuntimeCancelRequest, request: Request):
    if failure := _require_runtime_session(request, session_id):
        return failure
    result = await _call(request, "cancel", session_id, payload.turn_id)
    return (
        result
        if isinstance(result, JSONResponse)
        else {"session_id": session_id, "turn_id": payload.turn_id, "status": "cancelled"}
    )


@router.post("/sessions/{session_id}/approvals/{approval_id}")
async def runtime_approval(
    session_id: str, approval_id: str, payload: RuntimeApprovalRequest, request: Request
):
    if failure := _require_runtime_session(request, session_id):
        return failure
    result = await _call(request, "decide", session_id, approval_id, payload.decision)
    return (
        result
        if isinstance(result, JSONResponse)
        else {
            "session_id": session_id,
            "approval_id": approval_id,
            "decision": payload.decision,
            "status": "decided",
        }
    )
