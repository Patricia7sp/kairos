"""Terminal commands and renderer for the shared Agent Runtime host."""

from __future__ import annotations

import getpass
import json
import sys
from collections.abc import Mapping
from contextlib import aclosing
from pathlib import Path

from kairos_runtime import (
    RuntimeClient,
    RuntimeErrorInfo,
    RuntimeEvent,
    public_error,
    serve_runtime,
)
from kairos_runtime.recovery import TranscriptProjection, json_value
from kairos_runtime.wire import runtime_event_to_json

__all__ = ["render_runtime_event", "run_runtime"]


def _emit(value, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True), flush=True)
    elif isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
    else:
        print(value)


class RuntimeHumanRenderer:
    """One stream's projection; stdout corrections are explicitly labelled."""

    def __init__(self):
        self.projections: dict[tuple[str, str], TranscriptProjection] = {}
        self.seen: set[tuple[str, str]] = set()

    def render(self, event: RuntimeEvent) -> None:  # noqa: PLR0912 - canonical event variants remain explicit
        identity = (event.session_id, event.event_id)
        if identity in self.seen:
            return
        self.seen.add(identity)
        projection = self.projections.setdefault(
            (event.session_id, event.turn_id), TranscriptProjection()
        )
        before = projection.content
        projection.apply(event)
        payload = event.payload
        if event.kind == "text":
            item = payload.get("item")
            if isinstance(item, Mapping):
                if projection.content != before:
                    print(f"\n[resposta confirmada]\n{projection.content}", flush=True)
            elif isinstance(payload.get("delta"), str):
                print(payload["delta"], end="", flush=True)
        elif event.kind == "reconciled":
            if projection.content != before:
                print(f"\n[resposta reconciliada]\n{projection.content}", flush=True)
            snapshot = payload["snapshot"]
            for item in snapshot["items"]:
                if item.get("type") not in {"agentMessage", "userMessage", "reasoning"}:
                    self._tool(item)
            self._state(snapshot.get("state", "recovering"))
        elif event.kind == "tool":
            self._tool(payload.get("item", payload))
        elif event.kind == "approval_request":
            approval_id = payload.get("approval_id")
            print(
                f"\naprovação pendente {approval_id}; responda em outro terminal: "
                f"kairos runtime approve --session {event.session_id} "
                f"--approval {approval_id} --decision accept",
                flush=True,
            )
        elif event.kind in {"turn_state", "turn_start"}:
            self._state(payload.get("state", "running"))
        elif event.kind == "turn_end":
            content = payload.get("content")
            if isinstance(content, str) and content != projection.content:
                print(f"\n[resposta final]\n{content}", flush=True)
            self._state(payload.get("state", "completed"))
        elif event.kind == "error":
            print("falha no runtime", file=sys.stderr, flush=True)

    @staticmethod
    def _tool(item):
        print("\n[ferramenta] " + json.dumps(json_value(item), ensure_ascii=False), flush=True)

    @staticmethod
    def _state(state):
        print(f"\n[estado: {state}]", flush=True)


async def render_runtime_event(
    event: RuntimeEvent, *, as_json: bool, renderer: RuntimeHumanRenderer | None = None
) -> None:
    """Render a canonical event, optionally retaining a consumer's stream projection."""
    if as_json:
        _emit(runtime_event_to_json(event), as_json=True)
    else:
        (renderer or RuntimeHumanRenderer()).render(event)


async def run_runtime(*, home: Path, args) -> int:  # noqa: PLR0912 - mirrors CLI grammar
    command = getattr(args, "runtime_command", None)
    as_json = getattr(args, "json", False)
    if command == "serve":
        try:
            await serve_runtime(home)
            return 0
        except RuntimeErrorInfo as exc:
            return _report_error(exc, as_json=as_json)

    client = RuntimeClient(home / "run" / "runtime.sock")
    try:
        if command == "status":
            result = await client.status()
        elif command == "login":
            method = args.method
            api_key = getpass.getpass("OpenAI API key: ") if method == "apiKey" else None
            result = await client.account_login(method, api_key)
        elif command == "logout":
            await client.account_logout()
            result = {"status": "logged_out"}
        elif command == "session":
            if args.runtime_session_command == "create":
                result = await client.create(
                    args.cwd,
                    args.sandbox,
                    consent=args.consent,
                    source="cli",
                    parent_session_id=args.parent_session,
                    session_id=args.session_id,
                )
            elif args.runtime_session_command == "end":
                await client.end(args.session)
                result = {"session_id": args.session, "status": "ended"}
            else:
                return 2
        elif command == "approve":
            await client.decide(args.session, args.approval, args.decision)
            result = {
                "session_id": args.session,
                "approval_id": args.approval,
                "decision": args.decision,
                "status": "decided",
            }
        elif command == "cancel":
            await client.cancel(args.session, args.turn)
            result = {"session_id": args.session, "turn_id": args.turn, "status": "cancelled"}
        elif command == "watch":
            renderer = RuntimeHumanRenderer()
            async with aclosing(client.subscribe(args.session, args.cursor)) as subscription:
                async for event in subscription:
                    await render_runtime_event(event, as_json=as_json, renderer=renderer)
            return 0
        else:
            return 2
        _emit(result, as_json=as_json)
        return 0
    except RuntimeErrorInfo as exc:
        return _report_error(exc, as_json=as_json)
    finally:
        await client.aclose()


def _report_error(exc: RuntimeErrorInfo, *, as_json: bool) -> int:
    safe = public_error(exc.code)
    if as_json:
        _emit(
            {"error": safe["code"], "message": safe["message"], "retryable": safe["retryable"]},
            as_json=True,
        )
    else:
        print(safe["message"], file=sys.stderr, flush=True)
    return 1
