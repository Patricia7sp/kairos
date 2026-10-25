"""Decisão automática sob posse, com catálogo e persistência reais."""

import asyncio
import json
from contextlib import closing
from dataclasses import replace

import pytest
from test_chat_search_loop import RoundGateway, answer_round, collect
from test_interaction_skill_catalog import _close, _service, _view
from test_skill_runtime import skill_text, write_skill

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_contract import InteractionServiceError
from kairos_providers import (
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelSelectionResolver,
    ProviderError,
    ProviderErrorKind,
    ProviderModelRef,
)
from kairos_skills.catalog import SkillCatalogError
from kairos_state.repositories import SkillCatalogRepository


@pytest.fixture(autouse=True)
def allow_fixture_tools(monkeypatch):
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")


def auto_turn():
    return InteractionEnvelope("auto", "cli", "pedido", skills_catalog_auto=True)


@pytest.mark.parametrize("mode", ["auto", "on", "off"])
def test_invalid_selection_remains_an_error_for_each_catalog_mode(tmp_path, monkeypatch, mode):
    from kairos_providers import ModelSelectionContext
    from kairos_providers.selection import ModelSelectionUnavailableError

    async def scenario():
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway)
        service._context_loader.load = lambda *_: ModelSelectionContext(
            global_default=ProviderModelRef("openrouter", "missing")
        )
        monkeypatch.setattr(
            SkillCatalogRepository,
            "get",
            lambda *_args, **_kwargs: pytest.fail("consultou catálogo"),
        )
        try:
            envelope = InteractionEnvelope(
                "selection",
                "cli",
                "pedido",
                skills_catalog=mode == "on",
                skills_catalog_auto=mode == "auto",
            )
            with pytest.raises(ModelSelectionUnavailableError):
                await collect(service, envelope)
            assert gateway.requests == []
            assert gateway.preparations == 0
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
def test_auto_positive_reuses_snapshot_and_reads_on_demand(tmp_path, async_mode):
    async def scenario():
        text = skill_text(body="procedimento privado fictício")
        write_skill(tmp_path, text=text)
        gateway = RoundGateway([_view(), answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=async_mode)
        try:
            events = await collect(service, auto_turn())
            assert not any(e.kind == "turn_error" for e in events)
            first, second = gateway.requests
            index = [m for m in first.messages if m.role == "system"]
            assert len(index) == 1
            assert "Revise documentos." in index[0].content[0].value
            assert "procedimento privado" not in index[0].content[0].value
            assert [t["function"]["name"] for t in first.tools] == ["skill_view"]
            assert [m for m in second.messages if m.role == "system"] == index
            delivered = next(m for m in second.messages if m.role == "tool")
            assert json.loads(delivered.content[0].value)["text"] == text
            assert sum(e.kind == "skill_catalog_ready" for e in events) == 1
            assert not any(e.kind == "skill_catalog_skipped" for e in events)
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("tools", [False, None])
def test_auto_unsupported_model_skips_without_catalog_io(tmp_path, monkeypatch, async_mode, tools):
    async def scenario():
        from kairos_integration import skill_catalog_turn

        write_skill(tmp_path)
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=async_mode)
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=ProviderModelRef("openrouter", "test/model"),
                    display_name="Fictício",
                    capabilities=ModelCapabilities(chat=True, tools=tools),
                )
            ],
            origin=CatalogOrigin.CURATED,
        )
        service._resolver = ModelSelectionResolver(catalog)

        def forbidden(*_args, **_kwargs):
            pytest.fail("consultou catálogo ou handler para modelo inapto")

        monkeypatch.setattr(SkillCatalogRepository, "get", forbidden)
        monkeypatch.setattr(skill_catalog_turn, "capture_optional_skill_catalog", forbidden)
        monkeypatch.setattr(service, "_catalog_tool", forbidden)
        try:
            events = await collect(service, auto_turn())
            assert events[-1].kind == "turn_end"
            assert gateway.requests[0].tools == ()
            assert not any(m.role == "system" for m in gateway.requests[0].messages)
            skipped = [e for e in events if e.kind == "skill_catalog_skipped"]
            assert [e.catalog_skip_reason for e in skipped] == ["model_tools_unavailable"]
            kinds = [e.kind for e in events]
            assert (
                kinds.index("turn_start")
                < kinds.index("skill_catalog_skipped")
                < kinds.index("delta")
            )
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("empty", ["ausente", "vazio"])
def test_auto_empty_can_activate_later_but_positive_snapshot_stays_fixed(
    tmp_path, monkeypatch, async_mode, empty
):
    async def scenario():
        from kairos_integration import skill_catalog_turn

        if empty == "vazio":
            (tmp_path / "skills").mkdir()
        gateway = RoundGateway([answer_round(), answer_round(), answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=async_mode)
        turn = auto_turn()
        try:
            events = await collect(service, turn)
            assert [e.catalog_skip_reason for e in events if e.kind == "skill_catalog_skipped"] == [
                "empty"
            ]
            assert gateway.requests[-1].tools == ()
            assert db.execute("SELECT COUNT(*) FROM skill_catalogs").fetchone()[0] == 0
            path = write_skill(tmp_path)
            await collect(service, turn)
            index = next(m for m in gateway.requests[-1].messages if m.role == "system")
            path.write_text(skill_text().replace("Revise documentos.", "Descrição nova."))
            await collect(service, replace(turn, skills_catalog_auto=False))
            assert gateway.requests[-1].tools == ()
        finally:
            await _close(db, service)
        gateway = RoundGateway([answer_round()])
        db, restarted = _service(tmp_path, gateway, async_mode=async_mode)
        monkeypatch.setattr(
            skill_catalog_turn,
            "capture_optional_skill_catalog",
            lambda *_: pytest.fail("recapturou"),
        )
        try:
            await collect(restarted, turn)
            assert next(m for m in gateway.requests[0].messages if m.role == "system") == index
            assert "Descrição nova." not in index.content[0].value
        finally:
            await _close(db, restarted)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["digest", "orphan", "invalid-installation"])
def test_auto_corruption_and_invalid_installation_refuse_before_provider(
    tmp_path, monkeypatch, failure
):
    async def scenario():
        from kairos_integration import skill_catalog_turn

        write_skill(tmp_path)
        gateway = RoundGateway([_view(), answer_round(), answer_round()])
        db, service = _service(tmp_path, gateway)
        try:
            if failure == "invalid-installation":
                (tmp_path / "skills/revisar-docs/SKILL.md").write_text("conteúdo privado inválido")
            else:
                await collect(service, auto_turn())
                if failure == "digest":
                    with db:
                        db.execute("UPDATE skill_catalogs SET digest=?", ("0" * 64,))
                else:
                    db.execute("PRAGMA foreign_keys=OFF")
                    with db:
                        db.execute("DELETE FROM skill_catalogs")
                    db.execute("PRAGMA foreign_keys=ON")
                monkeypatch.setattr(
                    skill_catalog_turn,
                    "capture_optional_skill_catalog",
                    lambda *_: pytest.fail("recapturou corrupção"),
                )
            before = (
                len(gateway.requests),
                gateway.preparations,
                db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            )
            envelope = auto_turn()
            with pytest.raises(InteractionServiceError) as error:
                await collect(service, envelope)
            assert (
                len(gateway.requests),
                gateway.preparations,
                db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
            ) == before
            assert str(tmp_path) not in str(error.value)
            assert "conteúdo privado" not in str(error.value)
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_auto_retries_keep_one_selection_one_notice_and_one_index(tmp_path, monkeypatch):
    async def scenario():
        path = write_skill(tmp_path)

        class ChangingGateway(RoundGateway):
            async def stream(self, request):
                if len(self.requests) == 1:
                    path.write_text(skill_text(body="nova versão"))
                async for event in super().stream(request):
                    yield event

        gateway = ChangingGateway(
            [
                _view(),
                [ProviderError(ProviderErrorKind.NETWORK, "falha fictícia", retryable=True)],
                answer_round(),
            ]
        )
        db, service = _service(tmp_path, gateway)
        selections = []
        original = service._resolve_snapshot

        def resolve(envelope):
            result = original(envelope)
            selections.append(result)
            return result

        monkeypatch.setattr(service, "_resolve_snapshot", resolve)
        try:
            events = await collect(service, auto_turn())
            assert len(selections) == 1
            assert gateway.preparations == 1
            assert len(gateway.requests) == 3
            assert sum(e.kind == "skill_catalog_ready" for e in events) == 1
            blocks = [next(m for m in r.messages if m.role == "system") for r in gateway.requests]
            assert blocks[0] == blocks[1] == blocks[2]
            assert all(r.model == selections[0].ref for r in gateway.requests)
            assert (
                json.loads(
                    next(m for m in gateway.requests[-1].messages if m.role == "tool")
                    .content[0]
                    .value
                )["text"]
                == skill_text()
            )
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["cancel", "lost"])
def test_auto_capability_closes_on_cancel_or_lost_ownership(tmp_path, stop):
    async def scenario():
        from test_interaction_turn_ownership import ManualTime, RecordingBackend

        from kairos_integration.turn_ownership import SessionTurnOwnership, TurnLeaseLostError

        write_skill(tmp_path)
        started = asyncio.Event()
        captured = []

        class WaitingGateway(RoundGateway):
            async def stream(self, request):
                captured.append(service._turn_catalog.get())
                started.set()
                await asyncio.Event().wait()
                yield

        db, service = _service(tmp_path, WaitingGateway([]))
        if stop == "lost":
            timer = ManualTime()
            backend = RecordingBackend(timer.now, refresh_result=False)

            async def sleep(seconds):
                await started.wait()
                await timer.sleep(seconds)

            service._turn_ownership = SessionTurnOwnership(
                backend, clock=timer.now, sleep=sleep, ttl_seconds=6, refresh_interval=2
            )
        task = asyncio.create_task(collect(service, auto_turn()))
        try:
            await asyncio.wait_for(started.wait(), 2)
            if stop == "cancel":
                task.cancel()
            with pytest.raises(asyncio.CancelledError if stop == "cancel" else TurnLeaseLostError):
                await asyncio.wait_for(task, 3)
            with pytest.raises(SkillCatalogError):
                await captured[0].view("revisar-docs")
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await _close(db, service)

    asyncio.run(scenario())


def test_router_refuses_auto_catalog_intent_for_runtime(tmp_path):
    from test_interaction_router import make_router

    from kairos_runtime import RuntimeErrorInfo
    from kairos_state import connect
    from kairos_state.repositories import SessionRepository

    router, model, runtime = make_router(tmp_path)
    with closing(connect(tmp_path / "state.db")) as db, db:
        SessionRepository(db).ensure("runtime-session", source="cli")
        db.execute("UPDATE sessions SET execution_kind='agent_runtime' WHERE id='runtime-session'")

    async def scenario():
        try:
            envelope = InteractionEnvelope(
                "runtime-session",
                "cli",
                "pedido",
                skills_catalog_auto=True,
                idempotency_key="fixture",
            )
            with pytest.raises(RuntimeErrorInfo):
                await collect(router, envelope)
            assert model.envelopes == []
            assert runtime.submitted == []
        finally:
            await router.aclose()

    asyncio.run(scenario())
