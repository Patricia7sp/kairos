"""Seleção persistida entre interação por modelo e por agent runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from kairos_runtime import RuntimeErrorInfo, RuntimeEvent
from kairos_state import connect
from kairos_state.repositories import SessionRepository

from .interaction_contract import InteractionEnvelope, InteractionEvent

__all__ = ["InteractionRouter"]

_IDENTITY_PARAMETERS = frozenset(
    {
        "execution_kind",
        "runtime_kind",
        "external_thread_id",
        "cwd",
        "canonical_cwd",
        "sandbox",
        "sandbox_profile",
    }
)


class InteractionRouter:
    def __init__(self, home: Path, model_service: Any, runtime_client: Any) -> None:
        self.home = Path(home)
        self.model_service = model_service
        self.runtime_client = runtime_client
        self._closed = False

    async def stream(
        self, envelope: InteractionEnvelope
    ) -> AsyncIterator[InteractionEvent | RuntimeEvent]:
        if self._closed:
            raise RuntimeErrorInfo("unavailable", "roteador de interação encerrado", False)
        if self._execution_kind(envelope.conversation_id) != "agent_runtime":
            async for event in self.model_service.stream(envelope):
                yield event
            return
        self._validate_runtime_envelope(envelope)
        assert envelope.idempotency_key is not None
        turn_id = await self.runtime_client.submit(
            envelope.conversation_id, envelope.content, envelope.idempotency_key
        )
        async for event in self.runtime_client.subscribe(envelope.conversation_id):
            if event.turn_id != turn_id:
                continue
            yield event
            if event.kind == "turn_end":
                return

    def _execution_kind(self, session_id: str) -> str:
        connection = connect(self.home / "state.db")
        try:
            row = SessionRepository(connection).get(session_id)
            # sqlite3.Row membership inspects values, unlike Mapping membership.
            if row is None or "execution_kind" not in row.keys():  # noqa: SIM118
                return "model"
            return row["execution_kind"] or "model"
        finally:
            connection.close()

    @staticmethod
    def _validate_runtime_envelope(envelope: InteractionEnvelope) -> None:
        if (
            not isinstance(envelope.idempotency_key, str)
            or not envelope.idempotency_key.strip()
            or envelope.override is not None
            or any(key in envelope.parameters for key in _IDENTITY_PARAMETERS)
        ):
            raise RuntimeErrorInfo("invalid_event", "envelope de runtime inválido", False)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        results = []
        for resource in (self.model_service, self.runtime_client):
            try:
                await resource.aclose()
            except BaseException as exc:  # noqa: BLE001 - fecha ambos recursos próprios
                results.append(exc)
        if results:
            raise BaseExceptionGroup("falha ao fechar InteractionRouter", results)
