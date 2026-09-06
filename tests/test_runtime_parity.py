from __future__ import annotations

import asyncio
import json

from kairos_cli.runtime import render_runtime_event
from kairos_runtime import RuntimeEvent, runtime_event_to_json
from kairos_web.runtime_transport import runtime_event_to_json as web_runtime_event_to_json


def test_runtime_web_and_cli_share_the_one_public_wire_serializer(capsys):
    event = RuntimeEvent(1, "e1", "s1", "t1", 1, "v1:s1:1", "text", {"delta": "hello"})
    expected = runtime_event_to_json(event)
    websocket_payload = web_runtime_event_to_json(event)
    asyncio.run(render_runtime_event(event, as_json=True))
    cli_output_line = capsys.readouterr().out.strip()
    assert websocket_payload == expected
    assert json.loads(cli_output_line) == expected
    assert expected["execution_kind"] == "agent_runtime"
