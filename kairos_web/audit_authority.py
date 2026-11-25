"""Adaptador de despacho interno. Origem, autoridade e consumidor vêm do servidor."""

import json
import queue
import re
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request

from kairos_security.models import SecurityReport
from kairos_tools.audit_store import AuthorityDenied, require


async def dispatch_capture(request: Request, *, authority, origin: str, inbox):
    from kairos_web.server import SESSION_COOKIE, _token_ok

    configured = urlsplit(origin)
    require(
        request.method == "POST"
        and configured.scheme == "https"
        and configured.hostname
        and not configured.username
        and not (configured.path or configured.query or configured.fragment)
        and request.headers.getlist("origin") == [origin]
        and not request.scope.get("query_string")
    )
    credential = request.cookies.get(SESSION_COOKIE)
    require(credential and credential.isascii() and _token_ok(credential))
    require(request.headers.get("content-type", "").partition(";")[0] == "application/json")
    body = bytearray()
    async for chunk in request.stream():
        require(len(body) + len(chunk) <= 1024)
        body.extend(chunk)

    def unique(pairs):
        require(len(dict(pairs)) == len(pairs))
        return dict(pairs)

    try:
        selectors = json.loads(body, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        raise AuthorityDenied() from None
    require(
        isinstance(selectors, dict)
        and selectors.keys() == {"project", "revision", "grant"}
        and all(isinstance(v, str) for v in selectors.values())
    )
    require(
        re.fullmatch(
            r"[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}",
            selectors["project"],
        )
        and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", selectors["revision"])
        and re.fullmatch(r"gr_[a-f0-9]{64}", selectors["grant"])
    )
    await run_in_threadpool(authority.capture, "web:operador", inbox=inbox, **selectors)


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
