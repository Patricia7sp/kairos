"""Thin terminal transport over the canonical InteractionService."""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import aclosing
from dataclasses import dataclass
from pathlib import Path

from kairos_cli.runtime import render_runtime_event
from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionEventKind,
    interaction_event_to_json,
)
from kairos_integration import build_interaction_router as build_interaction_service
from kairos_integration.interaction_contract import InteractionServiceUnavailableError
from kairos_providers import ProviderModelRef
from kairos_runtime import RuntimeErrorInfo, RuntimeEvent, public_error
from kairos_runtime.wire import runtime_event_to_json

__all__ = ["ChatUsageError", "run_chat"]


class ChatUsageError(ValueError):
    """Invalid terminal-only arguments detected before service composition."""


@dataclass
class _HumanRenderer:
    line_open: bool = False
    failed: bool = False

    def emit(self, event: InteractionEvent) -> None:
        if event.kind is InteractionEventKind.DELTA:
            print(event.text, end="", flush=True)
            self.line_open = self.line_open or bool(event.text)
        elif event.kind is InteractionEventKind.TURN_ERROR:
            self.finish_line()
            print(event.error or "falha ao executar interação", file=sys.stderr, flush=True)
            self.failed = True
        elif event.kind is InteractionEventKind.TURN_END:
            self.finish_line()

    def finish_line(self) -> None:
        if self.line_open:
            print(flush=True)
            self.line_open = False


async def run_chat(
    *,
    home: Path,
    session_id: str,
    prompt: str,
    provider: str | None,
    model: str | None,
    as_json: bool,
    quiet: bool = False,
    idempotency_key: str | None = None,
) -> int:
    """Run a one-shot or interactive terminal session with one owned service graph."""
    session_id = _required_session_id(session_id)
    override = _model_override(provider, model)
    if idempotency_key is not None and not idempotency_key.strip():
        raise ChatUsageError("--idempotency-key exige um valor não vazio")
    if idempotency_key is not None and not prompt:
        raise ChatUsageError("--idempotency-key só pode ser usado com uma mensagem")
    runtime_session = _is_runtime_session(home, session_id)
    service = build_interaction_service(home)
    try:
        if prompt:
            return await _run_turn(
                service,
                session_id=session_id,
                content=prompt,
                override=override,
                as_json=as_json,
                idempotency_key=idempotency_key or (uuid.uuid4().hex if runtime_session else None),
            )
        return await _run_interactive(
            service,
            session_id=session_id,
            override=override,
            as_json=as_json,
            quiet=quiet,
            runtime_session=runtime_session,
        )
    except InteractionServiceUnavailableError as exc:
        print(exc.message, file=sys.stderr, flush=True)
        return 1
    except RuntimeErrorInfo as exc:
        print(public_error(exc.code)["message"], file=sys.stderr, flush=True)
        return 1
    finally:
        await service.aclose()


async def _run_interactive(
    service,
    *,
    session_id: str,
    override: ProviderModelRef | None,
    as_json: bool,
    quiet: bool,
    runtime_session: bool = False,
) -> int:
    if not quiet and not as_json:
        print("Kairos Agent CLI (digite 'sair' ou Ctrl+C para encerrar)")
    exit_code = 0
    while True:
        try:
            line = input("" if as_json or quiet else "\nvocê > ").strip()
        except EOFError:
            return exit_code
        if not line:
            continue
        if line.lower() in {"exit", "quit", "sair"}:
            return exit_code
        exit_code = max(
            exit_code,
            await _run_turn(
                service,
                session_id=session_id,
                content=line,
                override=override,
                as_json=as_json,
                idempotency_key=uuid.uuid4().hex if runtime_session else None,
            ),
        )


async def _run_turn(
    service,
    *,
    session_id: str,
    content: str,
    override: ProviderModelRef | None,
    as_json: bool,
    idempotency_key: str | None = None,
) -> int:
    envelope = InteractionEnvelope(
        conversation_id=session_id,
        source="cli",
        content=content,
        override=override,
        idempotency_key=idempotency_key,
    )
    renderer = _HumanRenderer()
    last_runtime_event: RuntimeEvent | None = None
    try:
        async for event in service.stream(envelope):
            if isinstance(event, RuntimeEvent):
                last_runtime_event = event
                if as_json:
                    print(
                        json.dumps(
                            runtime_event_to_json(event), ensure_ascii=False, sort_keys=True
                        ),
                        flush=True,
                    )
                else:
                    await render_runtime_event(event, as_json=False)
                if event.kind == "error":
                    renderer.failed = True
                continue
            if as_json:
                print(
                    json.dumps(
                        interaction_event_to_json(event, conversation_id=session_id),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    flush=True,
                )
                if event.kind is InteractionEventKind.TURN_ERROR:
                    renderer.failed = True
            else:
                renderer.emit(event)
    except asyncio.CancelledError:
        if last_runtime_event is not None:
            await _cancel_and_confirm(service, last_runtime_event, as_json=as_json)
        raise
    finally:
        renderer.finish_line()
    return 1 if renderer.failed else 0


def _model_override(provider: str | None, model: str | None) -> ProviderModelRef | None:
    if provider is None and model is None:
        return None
    if not provider or not provider.strip() or not model or not model.strip():
        raise ChatUsageError("--provider e --model devem ser informados juntos")
    return ProviderModelRef(provider.strip(), model.strip())


def _required_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ChatUsageError("--session exige um ID não vazio")
    return session_id.strip()


def _is_runtime_session(home: Path, session_id: str) -> bool:
    from kairos_state import connect

    db_path = Path(home) / "state.db"
    if not db_path.exists():
        return False
    connection = connect(db_path)
    try:
        row = connection.execute(
            "SELECT execution_kind FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        return row is not None and row["execution_kind"] == "agent_runtime"
    except Exception:  # noqa: BLE001 - legacy/uninitialized DB remains model-compatible
        return False
    finally:
        connection.close()


async def _cancel_and_confirm(service, event: RuntimeEvent, *, as_json: bool) -> None:
    """A Ctrl-C is a cancellation command; closing a stream alone is not."""
    client = getattr(service, "runtime_client", None)
    if client is None:
        return
    try:
        await client.cancel(event.session_id, event.turn_id)
        async with aclosing(client.subscribe(event.session_id, event.cursor)) as subscription:
            async for confirmation in subscription:
                if confirmation.turn_id != event.turn_id:
                    continue
                await render_runtime_event(confirmation, as_json=as_json)
                if confirmation.kind == "turn_end":
                    return
    except RuntimeErrorInfo as exc:
        print(public_error(exc.code)["message"], file=sys.stderr, flush=True)
