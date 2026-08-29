"""Paridade explícita entre as superfícies Web e CLI."""

from __future__ import annotations

import asyncio
import json

from kairos_cli.chat import _run_turn
from kairos_integration import InteractionEnvelope, InteractionEvent
from kairos_web.chat_transport import interaction_event_to_json


class EventService:
    def __init__(self, events: tuple[InteractionEvent, ...]) -> None:
        self.events = events

    async def stream(self, envelope: InteractionEnvelope):
        assert envelope.conversation_id == "parity-1"
        assert envelope.source == "cli"
        for event in self.events:
            yield event


def test_web_e_cli_emitem_o_mesmo_protocolo_versionado(capsys) -> None:
    events = (
        InteractionEvent(kind="delta", text="resposta"),
        InteractionEvent(kind="usage"),
        InteractionEvent.turn_end("stop"),
    )

    exit_code = asyncio.run(
        _run_turn(
            EventService(events),
            session_id="parity-1",
            content="pergunta",
            override=None,
            as_json=True,
        )
    )

    cli_payloads = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    web_payloads = [
        interaction_event_to_json(event, conversation_id="parity-1") for event in events
    ]
    assert exit_code == 0
    assert cli_payloads == web_payloads
