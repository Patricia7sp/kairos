"""Thin terminal transport over the canonical InteractionService."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionEventKind,
    build_interaction_service,
    interaction_event_to_json,
)
from kairos_providers import ProviderModelRef

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
) -> int:
    """Run a one-shot or interactive terminal session with one owned service graph."""
    session_id = _required_session_id(session_id)
    override = _model_override(provider, model)
    service = build_interaction_service(home)
    try:
        if prompt:
            return await _run_turn(
                service,
                session_id=session_id,
                content=prompt,
                override=override,
                as_json=as_json,
            )
        return await _run_interactive(
            service,
            session_id=session_id,
            override=override,
            as_json=as_json,
            quiet=quiet,
        )
    finally:
        await service.aclose()


async def _run_interactive(
    service,
    *,
    session_id: str,
    override: ProviderModelRef | None,
    as_json: bool,
    quiet: bool,
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
            ),
        )


async def _run_turn(
    service,
    *,
    session_id: str,
    content: str,
    override: ProviderModelRef | None,
    as_json: bool,
) -> int:
    envelope = InteractionEnvelope(
        conversation_id=session_id,
        source="cli",
        content=content,
        override=override,
    )
    renderer = _HumanRenderer()
    try:
        async for event in service.stream(envelope):
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
