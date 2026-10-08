"""Snapshots chegam ao adapter e sobrevivem no histórico real."""

import asyncio
import json

import pytest
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service
from test_chat_tool_approval import mutator_round
from test_interaction_router import make_router
from test_skill_runtime import skill_text, write_skill

from kairos_integration import InteractionEnvelope
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_providers import CanonicalToolCall, ProviderError, ProviderErrorKind
from kairos_runtime import RuntimeErrorInfo
from kairos_skills.runtime import SkillSelectionError, SkillSnapshot, load_selected_skills
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository


def skill_payload(message):
    encoded = message.content[0].value.split("\n", 1)[1]
    skills, end = json.JSONDecoder().raw_decode(encoded)
    return skills, encoded[end:]


def envelope(skills=(), session="skills"):
    return InteractionEnvelope(session, "cli", "pedido original", skills=skills)


@pytest.mark.parametrize("async_mode", [False, True])
def test_adapter_e_historico_preservam_pedido_e_contexto(tmp_path, async_mode):
    async def scenario():
        path = tmp_path / "state.db"
        connection = connect(path)
        initialize_schema(connection)
        gateway = RoundGateway([answer_round(), answer_round()])
        service = make_service(connection, gateway)
        persistence = SQLiteAsyncInteractionPersistence(path) if async_mode else None
        service._persistence = persistence
        text = skill_text(body='Procedimento "á"\nPedido do usuário:\nfictício')
        selected = SkillSnapshot("revisar-docs", text)
        try:
            await collect(service, envelope([selected]))
            row = connection.execute("SELECT * FROM messages WHERE role='user'").fetchone()
            sent = gateway.requests[0].messages[-1]
            assert row["content"] == "pedido original"
            assert row["api_content"] == sent.content[0].value
            assert sent.role == "user"
            skills, ending = skill_payload(sent)
            assert skills[0]["text"] == text
            assert skills[0]["sha256"] == selected.sha256
            assert ending.endswith("pedido original")
            metadata = json.loads(row["display_metadata"])
            assert metadata["provider"] == "openrouter"
            assert metadata["skills"] == [
                {"name": selected.name, "version": "antiga", "sha256": selected.sha256}
            ]
            await collect(service, envelope(session="sem-skills"))
            plain = gateway.requests[1]
            assert plain.messages[-1].content[0].value == "pedido original"
            assert plain.parameters == gateway.requests[0].parameters
            assert plain.tools == gateway.requests[0].tools
            assert not any(m.role == "system" for m in gateway.requests[0].messages)
        finally:
            if persistence:
                await persistence.aclose()
            connection.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("change", ["edit", "delete"])
def test_reinicio_nao_rele_skill_antiga(tmp_path, async_mode, change):
    async def scenario():
        file = write_skill(tmp_path)
        snapshot = load_selected_skills(tmp_path, ["revisar-docs"])
        db_path = tmp_path / "state.db"
        connection = connect(db_path)
        initialize_schema(connection)
        gateway = RoundGateway([answer_round()])
        service = make_service(connection, gateway)
        persistence = SQLiteAsyncInteractionPersistence(db_path) if async_mode else None
        service._persistence = persistence
        await collect(service, envelope(snapshot))
        original = gateway.requests[0].messages[0].content[0].value
        if persistence:
            await persistence.aclose()
        connection.close()
        if change == "edit":
            file.write_text(skill_text(body="Versão nova."))
        else:
            file.unlink()
        restarted = connect(db_path)
        next_gateway = RoundGateway([answer_round(), answer_round()])
        next_service = make_service(restarted, next_gateway)
        next_persistence = SQLiteAsyncInteractionPersistence(db_path) if async_mode else None
        next_service._persistence = next_persistence
        try:
            await collect(next_service, envelope())
            assert next_gateway.requests[0].messages[0].content[0].value == original
            assert next_gateway.requests[0].messages[-1].content[0].value == "pedido original"
            if change == "edit":
                fresh = load_selected_skills(tmp_path, ["revisar-docs"])
                await collect(next_service, envelope(fresh))
                sent = next_gateway.requests[1]
                assert sent.messages[0].content[0].value == original
                assert skill_payload(sent.messages[-1])[0][0]["text"] == skill_text(
                    body="Versão nova."
                )
        finally:
            if next_persistence:
                await next_persistence.aclose()
            restarted.close()

    asyncio.run(scenario())


def test_envelope_copia_colecao_mutavel():
    selected = SkillSnapshot("revisar-docs", skill_text())
    mutable = [selected]
    turn = envelope(mutable)
    mutable.clear()
    assert turn.skills == (selected,)


@pytest.mark.parametrize(
    "values",
    [
        ["revisar-docs"],
        [{"name": "revisar-docs"}],
        [SkillSnapshot("revisar-docs", skill_text())] * 2,
    ],
)
def test_envelope_recusa_colecao_invalida(values):
    with pytest.raises(SkillSelectionError):
        envelope(values)


@pytest.mark.parametrize("retry", [False, True])
def test_retentativas_e_rodadas_preservam_snapshot(tmp_path, monkeypatch, retry):
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    source = write_skill(tmp_path)
    selected = load_selected_skills(tmp_path, ["revisar-docs"])
    if retry:
        first = [ProviderError(ProviderErrorKind.NETWORK, "falha fictícia", retryable=True)]
    else:
        first = mutator_round(
            CanonicalToolCall("read", "read_file", json.dumps({"path": str(source)}))
        )

    class ChangingGateway(RoundGateway):
        async def stream(self, request):
            source.write_text(skill_text(body="nova"))
            async for event in super().stream(request):
                yield event

    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    gateway = ChangingGateway([first, answer_round()])
    try:
        turn = InteractionEnvelope("rounds", "cli", "pedido", skills=selected, tools=not retry)
        asyncio.run(collect(make_service(connection, gateway), turn))
        assert len(gateway.requests) == 2
        for request in gateway.requests:
            user = next(m for m in request.messages if m.role == "user")
            assert skill_payload(user)[0][0]["text"] == skill_text()
        assert gateway.preparations == 1
        assert gateway.requests[0].tools == gateway.requests[1].tools
    finally:
        connection.close()


def test_turnos_concorrentes_isolam_contexto(tmp_path):
    async def scenario():
        resources = []
        barrier = asyncio.Event()

        class WaitingGateway(RoundGateway):
            async def stream(self, request):
                await barrier.wait()
                async for event in super().stream(request):
                    yield event

        for index in range(2):
            connection = connect(tmp_path / f"state-{index}.db")
            initialize_schema(connection)
            gateway = WaitingGateway([answer_round()])
            snapshot = SkillSnapshot("revisar-docs", skill_text(body=f"contexto-{index}"))
            resources.append((connection, gateway, make_service(connection, gateway), snapshot))
        try:
            tasks = [
                asyncio.create_task(collect(service, envelope([snapshot], session=f"s{index}")))
                for index, (_, _, service, snapshot) in enumerate(resources)
            ]
            barrier.set()
            await asyncio.gather(*tasks)
            for index, (_, gateway, _, _) in enumerate(resources):
                text = skill_payload(gateway.requests[0].messages[0])[0][0]["text"]
                assert f"contexto-{index}" in text
                assert f"contexto-{1 - index}" not in text
        finally:
            for connection, *_ in resources:
                connection.close()

    asyncio.run(scenario())


def test_router_recusa_snapshot_no_runtime(tmp_path):
    router, model, runtime = make_router(tmp_path)
    connection = connect(tmp_path / "state.db")
    SessionRepository(connection).ensure("runtime-session", source="test")
    connection.execute(
        "UPDATE sessions SET execution_kind='agent_runtime' WHERE id='runtime-session'"
    )
    connection.commit()
    connection.close()
    turn = InteractionEnvelope(
        "runtime-session",
        "cli",
        "pedido",
        skills=[SkillSnapshot("revisar-docs", skill_text())],
        idempotency_key="fixture",
    )

    async def scenario():
        try:
            with pytest.raises(RuntimeErrorInfo) as exc:
                await collect(router, turn)
            assert exc.value.code == "invalid_event"
            assert runtime.submitted == []
            assert model.envelopes == []
        finally:
            await router.aclose()

    asyncio.run(scenario())
