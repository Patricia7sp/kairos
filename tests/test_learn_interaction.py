"""/learn atravessa o loop real, persistência e publicação de skills."""

import asyncio
import json
from dataclasses import replace

import pytest
from skill_mutation_fixtures import creation_text
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service
from test_chat_tool_approval import mutator_round

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_contract import InteractionServiceError
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_providers import CanonicalToolCall, CatalogOrigin, ProviderError, ProviderErrorKind
from kairos_skills.learn import build_learn_prompt
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.removal_state import database_connection, read_skill_repository
from kairos_state import connect, initialize_schema


def call(identifier="create", name="revisar-docs"):
    return CanonicalToolCall(
        identifier, "skill_create", json.dumps({"name": name, "skill_md": creation_text(name)})
    )


def learn(**kwargs):
    return InteractionEnvelope(
        "learn-session", "cli", "/learn revisar", learn_request="revisar", **kwargs
    )


async def confirmed(service, envelope, answer=None):
    events = []
    async for event in service.stream(envelope):
        events.append(event)
        if event.kind == "tool_approval_request":
            proposal = service.skill_creation_proposal(
                approval_id=event.tool_approval_id, session_id=envelope.conversation_id
            )
            with pytest.raises(InteractionServiceError):
                service.decide_tool_approval(
                    approval_id=proposal.id, session_id=envelope.conversation_id, decision="allow"
                )
            service.decide_skill_creation(
                approval_id=proposal.id,
                session_id=envelope.conversation_id,
                sha256=proposal.sha256,
                answer=answer or f"criar {proposal.name}",
            )
    return events


@pytest.mark.parametrize("automatic", [False, True])
@pytest.mark.parametrize("async_mode", [False, True])
def test_single_commit_transient_prompt_and_next_normal_turn(
    tmp_path, monkeypatch, automatic, async_mode
):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        gateway = RoundGateway(
            [
                mutator_round(call(), call("second", "other-skill")),
                mutator_round(call("third", "third-skill")),
                answer_round(),
                answer_round(),
            ]
        )
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        if async_mode:
            service._persistence = SQLiteAsyncInteractionPersistence(tmp_path / "state.db")
        try:
            events = await confirmed(service, learn(skills_catalog_auto=automatic))
            assert not any(e.kind == "turn_error" for e in events)
            assert len([e for e in events if e.kind == "tool_approval_request"]) == 1
            results = [e.tool_result for e in events if e.kind == "tool_result"]
            assert [r.is_error for r in results] == [False, True, True]
            receipt = json.loads(results[0].content)
            assert receipt["state"] == "committed"
            assert (tmp_path / "skills/revisar-docs/SKILL.md").read_text() == creation_text()
            with database_connection(tmp_path) as connection:
                records = read_skill_repository(connection, tmp_path).history()
                assert len(records) == 1 and records[0].operation_id == receipt["operation_id"]
            prompt = build_learn_prompt("revisar")
            for request in gateway.requests:
                assert [t["function"]["name"] for t in request.tools] == ["skill_create"]
                assert not any(m.role == "system" for m in request.messages)
                assert any(
                    m.role == "user" and m.content[0].value == prompt for m in request.messages
                )
            row = db.execute(
                "SELECT content,api_content FROM messages WHERE role='user'"
            ).fetchone()
            assert tuple(row) == ("/learn revisar", "/learn revisar")
            await collect(
                service, InteractionEnvelope("learn-session", "cli", "obrigado", tools=True)
            )
            plain = gateway.requests[-1]
            assert "skill_create" not in [t["function"]["name"] for t in plain.tools]
            assert not any(prompt == part.value for m in plain.messages for part in m.content)
            assert any(
                m.role == "user" and m.content[0].value == "/learn revisar" for m in plain.messages
            )
        finally:
            if service._persistence is not None:
                await service._persistence.aclose()
            db.close()

    asyncio.run(scenario())


def test_no_catalog_preparation_even_with_explicit_catalog(tmp_path, monkeypatch):
    async def scenario():
        async def forbidden(*_, **__):
            raise AssertionError("catálogo não deve ser preparado")

        monkeypatch.setattr(
            "kairos_integration.interaction_service.prepare_skill_catalog_turn", forbidden
        )
        gateway = RoundGateway([answer_round(), answer_round()])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            events = await collect(service, learn(tools=True, skills_catalog=True))
            assert events[-1].kind == "turn_end"
            assert [t["function"]["name"] for t in gateway.requests[0].tools] == ["skill_create"]
            assert not any(m.role == "system" for m in gateway.requests[0].messages)
            assert not (tmp_path / "skills").exists()
            await collect(service, InteractionEnvelope("learn-session", "cli", "resposta"))
            assert gateway.requests[-1].tools == ()
        finally:
            db.close()

    asyncio.run(scenario())


def test_retry_uses_transient_prompt_and_clarification_drops_capability(tmp_path):
    async def scenario():
        gateway = RoundGateway(
            [
                [ProviderError(ProviderErrorKind.NETWORK, "fixture", retryable=True)],
                answer_round(),
                mutator_round(call()),
                answer_round(),
            ]
        )
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            await collect(service, learn())
            assert gateway.requests[0].messages == gateway.requests[1].messages
            assert gateway.requests[0].messages[-1].content[0].value == build_learn_prompt(
                "revisar"
            )
            events = await collect(
                service, InteractionEnvelope("learn-session", "cli", "esclarecimento")
            )
            assert not any(e.kind == "tool_approval_request" for e in events)
            assert next(e.tool_result for e in events if e.kind == "tool_result").is_error
            assert not (tmp_path / "skills").exists()
            assert (
                not db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='skill_mutations'"
                ).fetchone()
                or not db.execute("SELECT 1 FROM skill_mutations").fetchone()
            )
        finally:
            db.close()

    asyncio.run(scenario())


def test_source_and_model_tools_are_required_before_provider(tmp_path):
    with pytest.raises(ValueError):
        replace(learn(), source="web")

    async def scenario():
        gateway = RoundGateway([answer_round()])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        model = service._resolver._catalog.find(
            service._context_loader.load(learn()).global_default
        )
        service._resolver._catalog.merge(
            [replace(model, capabilities=replace(model.capabilities, tools=False))],
            origin=CatalogOrigin.CURATED,
        )
        try:
            with pytest.raises(InteractionServiceError):
                await collect(service, learn())
            assert gateway.requests == []
            assert not db.execute("SELECT 1 FROM messages WHERE role='user'").fetchone()
        finally:
            db.close()

    asyncio.run(scenario())


def test_cancel_pending_creation_revokes_proposal_without_skill_write(tmp_path):
    async def scenario():
        gateway = RoundGateway([mutator_round(call())])
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        stream = service.stream(learn())
        try:
            async for event in stream:
                if event.kind == "tool_approval_request":
                    approval_id = event.tool_approval_id
                    break
            await stream.aclose()
            with pytest.raises((InteractionServiceError, SkillMutationError)):
                service.skill_creation_proposal(approval_id=approval_id, session_id="learn-session")
            assert not (tmp_path / "skills").exists()
        finally:
            db.close()

    asyncio.run(scenario())


def test_fixed_catalog_and_active_conversation_survive_learn(tmp_path, monkeypatch):
    from test_skill_runtime import write_skill

    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(tmp_path)
        gateway = RoundGateway(
            [answer_round(), mutator_round(call(name="new-skill")), answer_round(), answer_round()]
        )
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            first = InteractionEnvelope(
                "learn-session", "cli", "procedimento anterior", skills_catalog=True
            )
            await collect(service, first)
            original = service._skill_catalogs.export_session("learn-session")
            await confirmed(service, learn())
            authoring = gateway.requests[1]
            assert authoring.messages[0].role == "user"
            assert authoring.messages[0].content[0].value == "procedimento anterior"
            assert not any(m.role == "system" for m in authoring.messages)
            assert service._skill_catalogs.export_session("learn-session") == original
            await collect(service, replace(first, content="continue"))
            assert service._skill_catalogs.export_session("learn-session") == original
            assert "new-skill" not in gateway.requests[-1].messages[0].content[0].value
            assert [t["function"]["name"] for t in gateway.requests[-1].tools] == ["skill_view"]
        finally:
            db.close()

    asyncio.run(scenario())


def test_approval_timeout_closes_creator_without_publication(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(
            "kairos_integration.interaction_service.TOOL_APPROVAL_TIMEOUT_SECONDS", 0.01
        )
        gateway = RoundGateway(
            [mutator_round(call()), mutator_round(call("second", "other")), answer_round()]
        )
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        service = make_service(db, gateway, skill_catalog_home=tmp_path)
        try:
            events = await collect(service, learn())
            assert len([e for e in events if e.kind == "tool_approval_request"]) == 1
            assert all(e.tool_result.is_error for e in events if e.kind == "tool_result")
            assert not (tmp_path / "skills").exists()
        finally:
            db.close()

    asyncio.run(scenario())


def test_two_simultaneous_homes_keep_proposals_and_writes_isolated(tmp_path, monkeypatch):
    import threading

    from kairos_skills.mutations import SkillMutationService

    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        barrier = threading.Barrier(2)
        original = SkillMutationService.add_text

        def simultaneous(service, *args, **kwargs):
            barrier.wait(timeout=2)
            return original(service, *args, **kwargs)

        monkeypatch.setattr(SkillMutationService, "add_text", simultaneous)
        ready = asyncio.Barrier(2)
        services, connections, proposals = [], [], [None, None]
        for index in range(2):
            home = tmp_path / str(index)
            home.mkdir()
            db = connect(home / "state.db")
            initialize_schema(db)
            text = creation_text(body="Procedimento da instalação " + str(index))
            create = CanonicalToolCall(
                "create", "skill_create", json.dumps({"name": "revisar-docs", "skill_md": text})
            )
            services.append(
                make_service(
                    db,
                    RoundGateway([mutator_round(create), answer_round()]),
                    skill_catalog_home=home,
                )
            )
            connections.append(db)

        async def run_one(index):
            service = services[index]
            async for event in service.stream(learn()):
                if event.kind == "tool_approval_request":
                    proposal = service.skill_creation_proposal(
                        approval_id=event.tool_approval_id, session_id="learn-session"
                    )
                    proposals[index] = proposal
                    await ready.wait()
                    with pytest.raises(SkillMutationError):
                        service.skill_creation_proposal(
                            approval_id=proposals[1 - index].id, session_id="learn-session"
                        )
                    service.decide_skill_creation(
                        approval_id=proposal.id,
                        session_id="learn-session",
                        sha256=proposal.sha256,
                        answer="criar revisar-docs",
                    )
                if event.kind == "tool_result":
                    assert not event.tool_result.is_error

        try:
            await asyncio.wait_for(asyncio.gather(run_one(0), run_one(1)), 5)
            for index in range(2):
                path = tmp_path / str(index) / "skills/revisar-docs/SKILL.md"
                assert path.read_text() == creation_text(
                    body="Procedimento da instalação " + str(index)
                )
        finally:
            for db in connections:
                db.close()

    asyncio.run(scenario())


def test_sqlite_heartbeat_refreshes_while_skill_writer_is_blocked(tmp_path, monkeypatch):
    import threading

    from kairos_integration.turn_ownership import (
        LeaseRefreshResult,
        SessionTurnOwnership,
        SQLiteAsyncTurnLeaseBackend,
    )
    from kairos_skills.mutations import SkillMutationService

    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        observed_deadlines = []
        db = connect(tmp_path / "state.db")
        initialize_schema(db)
        original = SkillMutationService.add_text

        def blocked(service, *args, **kwargs):
            entered.set()
            try:
                if not release.wait(2):
                    raise OSError("heartbeat não progrediu durante I/O")
                return original(service, *args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(SkillMutationService, "add_text", blocked)

        class ObservedBackend(SQLiteAsyncTurnLeaseBackend):
            async def refresh(self, conversation_id, holder, *, ttl_seconds):
                before = db.execute(
                    "SELECT expires_at FROM session_turn_leases WHERE conversation_id=?",
                    (conversation_id,),
                ).fetchone()[0]
                result = await super().refresh(conversation_id, holder, ttl_seconds=ttl_seconds)
                if (
                    entered.is_set()
                    and not finished.is_set()
                    and result.result is LeaseRefreshResult.REFRESHED
                ):
                    after = db.execute(
                        "SELECT expires_at FROM session_turn_leases WHERE conversation_id=?",
                        (conversation_id,),
                    ).fetchone()[0]
                    observed_deadlines.append((before, after))
                    release.set()
                return result

        service = make_service(
            db, RoundGateway([mutator_round(call()), answer_round()]), skill_catalog_home=tmp_path
        )
        service._turn_ownership = SessionTurnOwnership(
            ObservedBackend(tmp_path / "state.db"), ttl_seconds=1, refresh_interval=0.01
        )
        try:
            events = await asyncio.wait_for(confirmed(service, learn()), 5)
            assert not next(e.tool_result for e in events if e.kind == "tool_result").is_error
            assert observed_deadlines and all(
                after > before for before, after in observed_deadlines
            )
            assert not db.execute("SELECT 1 FROM session_turn_leases").fetchone()
        finally:
            release.set()
            db.close()

    asyncio.run(scenario())
