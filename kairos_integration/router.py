"""Seleção persistida entre interação por modelo e por agent runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
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
        self,
        envelope: InteractionEnvelope,
        *,
        on_runtime_accepted: Callable[[str], None] | None = None,
    ) -> AsyncIterator[InteractionEvent | RuntimeEvent]:
        if self._closed:
            raise RuntimeErrorInfo("unavailable", "roteador de interação encerrado", False)
        if self._execution_kind(envelope.conversation_id) != "agent_runtime":
            async for event in self.model_service.stream(envelope):
                yield event
            return
        self._validate_runtime_envelope(envelope)
        assert envelope.idempotency_key is not None
        client = self.runtime_client
        missing = [name for name in ("submit", "subscribe") if not hasattr(client, name)]
        if missing:
            # Fail-closed: sem os recursos do host de runtime, a sessão de
            # agent runtime NÃO cai para o modo model nem explode em
            # AttributeError — recusa nomeada, e o requisitante decide.
            raise RuntimeErrorInfo(
                "unavailable",
                f"host de runtime indisponível (sem {' e '.join(missing)})",
                False,
            )
        turn_id = await client.submit(
            envelope.conversation_id, envelope.content, envelope.idempotency_key
        )
        if on_runtime_accepted is not None:
            on_runtime_accepted(turn_id)
        async for event in client.subscribe(envelope.conversation_id):
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
            or envelope.web_search
            or envelope.tools
            or envelope.skills
            or envelope.skills_catalog
            or any(key in envelope.parameters for key in _IDENTITY_PARAMETERS)
        ):
            raise RuntimeErrorInfo("invalid_event", "envelope de runtime inválido", False)

    def decide_tool_approval(
        self,
        *,
        approval_id: str,
        session_id: str,
        decision: str,
    ) -> None:
        """Repassa a decisão do Chat ao serviço de modelo; nunca bloqueia."""
        decide = getattr(self.model_service, "decide_tool_approval", None)
        if decide is None:
            raise RuntimeErrorInfo("unavailable", "serviço de interação indisponível", False)
        decide(approval_id=approval_id, session_id=session_id, decision=decision)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        results = []
        for resource in (self.model_service, self.runtime_client):
            if resource is None:
                continue
            try:
                await resource.aclose()
            except BaseException as exc:  # noqa: BLE001 - fecha ambos recursos próprios
                results.append(exc)
        if results:
            raise BaseExceptionGroup("falha ao fechar InteractionRouter", results)
