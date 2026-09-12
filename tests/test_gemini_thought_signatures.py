"""Gemini function-call signatures survive tool rounds and durable history."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from kairos_integration import InteractionEnvelope, InteractionToolResult, interaction_event_to_json
from kairos_integration.interaction_service import InteractionService
from kairos_providers import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelSelectionContext,
    ProviderModelRef,
)
from kairos_providers.adapters.gemini_native import GeminiNativeAdapter
from kairos_providers.selection import ModelSelectionResolver
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository


def response(parts):
    document = {"candidates": [{"content": {"parts": parts}, "finishReason": "STOP"}]}
    return httpx.Response(200, content=f"data: {json.dumps(document)}\n\n".encode())


def service_for(db, adapter):
    ref = ProviderModelRef("gemini", "gemini-3-flash-preview")
    catalog = ModelCatalog()
    catalog.merge(
        [
            CatalogModel(
                ref=ref,
                display_name="Gemini",
                capabilities=ModelCapabilities(chat=True, tools=True),
            )
        ],
        origin=CatalogOrigin.CURATED,
    )
    return InteractionService(
        gateway=SimpleNamespace(create_adapter=lambda _: adapter),
        resolver=ModelSelectionResolver(catalog),
        context_loader=SimpleNamespace(load=lambda _: ModelSelectionContext(global_default=ref)),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
    )


def test_real_gemini_parallel_and_sequential_rounds_preserve_signatures_after_reopen(
    tmp_path, monkeypatch
):
    first_signature = "opaque-first+/=\nunchanged"
    next_signature = "opaque-next+/="
    first_parts = [
        {
            "functionCall": {"id": "a", "name": "web_search", "args": {"query": "Paris"}},
            "thoughtSignature": first_signature,
        },
        {"functionCall": {"id": "b", "name": "web_search", "args": {"query": "London"}}},
    ]
    next_parts = [
        {
            "functionCall": {"id": "c", "name": "web_search", "args": {"query": "Compare"}},
            "thoughtSignature": next_signature,
        }
    ]
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return response(first_parts)
        model_calls = [
            [part for part in message["parts"] if "functionCall" in part]
            for message in payload["contents"]
            if message["role"] == "model"
            and any("functionCall" in part for part in message["parts"])
        ]
        expected = [first_parts] if len(requests) == 2 else [first_parts, next_parts]
        if model_calls != expected:
            return httpx.Response(400, json={"error": "Missing or altered thought signature"})
        if len(requests) == 2:
            return response(next_parts)
        return response([{"text": "Completed with preserved reasoning context."}])

    async def search(call):
        return InteractionToolResult(call.id, '{"results":[]}')

    monkeypatch.setattr("kairos_integration.interaction_service.execute_web_search", search)

    async def scenario():
        db_path = tmp_path / "state.db"
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
            adapter = GeminiNativeAdapter(http, api_key="fixture-key")
            db = connect(db_path)
            initialize_schema(db)
            try:
                service = service_for(db, adapter)
                events = [
                    event
                    async for event in service.stream(
                        InteractionEnvelope(
                            conversation_id="signed",
                            source="web",
                            content="Compare",
                            web_search=True,
                        )
                    )
                ]
                assert events[-1].kind == "turn_end", events[-1]
                assert len(requests) == 3
                assert "Completed" in "".join(event.text for event in events)
                tool_calls = [event.tool_call for event in events if event.kind == "tool_call"]
                assert [call.thought_signature for call in tool_calls] == [
                    first_signature,
                    None,
                    next_signature,
                ]
                assert "opaque-first" not in repr(tool_calls)
                public = json.dumps([interaction_event_to_json(event) for event in events])
                assert all(
                    marker not in public
                    for marker in ("thought_signature", "opaque-first", "opaque-next")
                )
                stored = db.execute(
                    "SELECT tool_calls FROM messages WHERE role='assistant' AND tool_calls IS NOT NULL ORDER BY id"
                ).fetchall()
                assert json.loads(stored[0][0])[0]["thought_signature"] == first_signature
                assert json.loads(stored[1][0])[0]["thought_signature"] == next_signature
            finally:
                db.close()
            reopened = connect(db_path)
            try:
                service = service_for(reopened, adapter)
                events = [
                    event
                    async for event in service.stream(
                        InteractionEnvelope(
                            conversation_id="signed", source="web", content="Continue"
                        )
                    )
                ]
                assert events[-1].kind == "turn_end", events[-1]
                assert len(requests) == 4
                assert "tools" not in requests[-1]
            finally:
                reopened.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("signature", [None, False, 42, [], {"unexpected": "value"}])
def test_history_ignores_nonstring_signature_without_losing_tool_call(signature):
    calls = InteractionService._rehydrate_tool_calls(
        json.dumps(
            [{"id": "old", "name": "web_search", "arguments": "{}", "thought_signature": signature}]
        )
    )
    assert len(calls) == 1
    assert calls[0].id == "old"
    assert calls[0].thought_signature is None


def test_history_without_signature_remains_compatible():
    calls = InteractionService._rehydrate_tool_calls(
        '[{"id":"old","name":"web_search","arguments":"{}"}]'
    )
    assert len(calls) == 1
    assert calls[0].thought_signature is None
