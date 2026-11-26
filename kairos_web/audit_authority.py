"""Adaptador de despacho interno. Origem, autoridade e consumidor vêm do servidor."""

import json
import queue
import re
import sqlite3
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request

from kairos_security.models import SecurityReport
from kairos_tools.audit_store import AuthorityDenied, require

_PRINCIPAL = "web:operador"


async def _authenticated_json(request: Request, origin: str):
    from kairos_web.server import SESSION_COOKIE, _token_ok

    configured = urlsplit(origin)
    require(
        request.method == "POST"
        and len(origin) <= 256
        and origin.isascii()
        and configured.scheme == "https"
        and configured.hostname
        and configured.username is None
        and not (configured.path or configured.query or configured.fragment)
        and request.headers.getlist("origin") == [origin]
        and not request.scope.get("query_string")
    )
    credential = request.cookies.get(SESSION_COOKIE)
    require(credential and credential.isascii() and _token_ok(credential))
    require(request.headers.getlist("content-type") == ["application/json"])
    body = bytearray()

    def unique(pairs):
        require(len(dict(pairs)) == len(pairs))
        return dict(pairs)

    try:
        async for chunk in request.stream():
            require(len(body) + len(chunk) <= 1024)
            body.extend(chunk)
        data = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    except (ClientDisconnect, ValueError, RecursionError):
        raise AuthorityDenied() from None
    require(isinstance(data, dict))
    return data


def _capture_selectors(selectors):
    require(
        selectors.keys() == {"project", "revision", "grant"}
        and all(type(v) is str for v in selectors.values())
    )
    require(
        re.fullmatch(
            r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}",
            selectors["project"],
        )
        and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", selectors["revision"])
        and re.fullmatch(r"gr_[a-f0-9]{64}", selectors["grant"])
    )
    return selectors


async def dispatch_capture(request: Request, *, authority, origin: str, inbox):
    selectors = _capture_selectors(await _authenticated_json(request, origin))
    await run_in_threadpool(authority.capture, _PRINCIPAL, inbox=inbox, **selectors)


async def dispatch_snapshot_audit(request: Request, *, authority, origin: str) -> SecurityReport:
    """Despacho interno: consumir somente a cópia entregue pela autoridade."""
    from kairos_security.snapshot import audit_snapshot

    inbox = queue.Queue(maxsize=1)
    await dispatch_capture(request, authority=authority, origin=origin, inbox=inbox)
    try:
        snapshot = inbox.get_nowait()
    except queue.Empty:
        raise AuthorityDenied() from None
    return await run_in_threadpool(audit_snapshot, snapshot)


def _selection(data, **refs):
    require(
        all(
            type(data.get(field)) is str and re.fullmatch(prefix + r"_[a-f0-9]{64}", data[field])
            for field, prefix in refs.items()
        )
    )


async def dispatch_audit_exception(request: Request, *, authority, origin: str, action: str):
    """O chamador interno seleciona a ação; o cliente envia somente o schema correspondente."""
    from kairos_security import audit_exceptions

    data = await _authenticated_json(request, origin)
    match action:
        case "observe":
            handler = audit_exceptions.observe
            _capture_selectors(data)
        case "open_proof":
            handler = audit_exceptions.open_proof
            require(data.keys() == {"run_id"})
            _selection(data, run_id="run")
        case "prepare":
            handler = audit_exceptions.prepare
            require(data.keys() == {"run_id", "finding_ref", "reason_code", "expires_at"})
            _selection(data, run_id="run", finding_ref="finding")
            require(
                type(data["reason_code"]) is str
                and data["reason_code"] in audit_exceptions.REASONS
                and type(data["expires_at"]) is int
                and 0 < data["expires_at"] < 2**33
            )
            data["origin"] = origin
        case "confirm":
            handler = audit_exceptions.confirm
            require(data.keys() == {"intent_ref"})
            _selection(data, intent_ref="intent")
            data["origin"] = origin
        case "revoke":
            handler = audit_exceptions.revoke
            require(data.keys() == {"exception_id"})
            _selection(data, exception_id="exception")
        case _:
            raise AuthorityDenied()
    try:
        return await run_in_threadpool(handler, authority, _PRINCIPAL, **data)
    except (OSError, ValueError, sqlite3.Error, audit_exceptions.CapacityExceeded):
        raise AuthorityDenied() from None
