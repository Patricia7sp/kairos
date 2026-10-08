"""Índice e páginas atravessam o serviço e chegam ao adapter fictício."""

import asyncio
import contextvars
import json
import os
import threading
from dataclasses import replace

import pytest
from test_chat_search_loop import RoundGateway, answer_round, collect, make_service
from test_chat_tool_approval import mutator_round
from test_skill_runtime import skill_text, write_skill

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_contract import InteractionServiceError
from kairos_integration.persistence import SQLiteAsyncInteractionPersistence
from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelSelectionResolver,
    ProviderError,
    ProviderErrorKind,
    ProviderModelRef,
)
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SkillCatalogRepository


def _service(home, gateway, *, async_mode=False):
    db = connect(home / "state.db")
    initialize_schema(db)
    service = make_service(db, gateway)
    service._skill_catalog_home = home
    service._skill_catalogs = None if async_mode else SkillCatalogRepository(db)
    service._persistence = (
        SQLiteAsyncInteractionPersistence(home / "state.db") if async_mode else None
    )
    return db, service


def _view(reference=None, **arguments):
    values = {"name": "revisar-docs", **arguments}
    if reference is not None:
        values["reference"] = reference
    return mutator_round(CanonicalToolCall("view", "skill_view", json.dumps(values)))


async def _close(db, service):
    if service._persistence:
        await service._persistence.aclose()
    db.close()


@pytest.mark.parametrize("async_mode", [False, True])
def test_indice_sem_corpos_e_leitura_chega_ao_request_seguinte(tmp_path, monkeypatch, async_mode):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path, text=skill_text(body="corpo fictício á🙂"))
        gateway = RoundGateway([_view(), answer_round(), answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=async_mode)
        try:
            envelope = InteractionEnvelope("s1", "cli", "pedido original", skills_catalog=True)
            events = await collect(service, envelope)
            assert not any(e.kind == "turn_error" for e in events)
            assert len(gateway.requests) == 2
            first, second = gateway.requests
            index = [m for m in first.messages if m.role == "system"]
            assert len(index) == 1
            assert "Revise documentos." in index[0].content[0].value
            assert "corpo fictício" not in index[0].content[0].value
            assert [t["function"]["name"] for t in first.tools] == ["skill_view"]
            body = next(m for m in second.messages if m.role == "tool")
            assert json.loads(body.content[0].value)["text"] == path.read_text(encoding="utf-8")
            assert [m for m in second.messages if m.role == "system"] == index
            row = db.execute(
                "SELECT content,api_content FROM messages WHERE role='user'"
            ).fetchone()
            assert tuple(row) == ("pedido original", "pedido original")
            notices = [e.catalog_notice for e in events if e.kind == "skill_catalog_ready"]
            saved = SkillCatalogRepository(db).export_session("s1")
            assert len(notices) == 1
            assert notices[0].digest == saved["digest"]
            assert notices[0].entries == 1
            await collect(service, replace(envelope, skills_catalog=False))
            plain = gateway.requests[-1]
            assert plain.tools == ()
            assert not any(m.role == "system" for m in plain.messages)
            assert any(m.role == "tool" for m in plain.messages)
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("async_mode", [False, True])
def test_reinicio_asset_cacheado_versus_divergente(tmp_path, monkeypatch, async_mode):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path)
        refs = path.parent / "references"
        refs.mkdir()
        ref = refs / "guia.md"
        ref.write_text("original", encoding="utf-8")
        first_gateway = RoundGateway([_view(), answer_round()])
        db, first = _service(tmp_path, first_gateway, async_mode=async_mode)
        turn = InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
        try:
            await collect(first, turn)
            initial_system = first_gateway.requests[0].messages[0]
        finally:
            await _close(db, first)
        path.unlink()
        ref.write_text("alterada", encoding="utf-8")
        gateway = RoundGateway(
            [_view(), answer_round(), _view("references/guia.md"), answer_round()]
        )
        db, restarted = _service(tmp_path, gateway, async_mode=async_mode)
        try:
            events = await collect(restarted, turn)
            result = next(e.tool_result for e in events if e.kind == "tool_result")
            assert json.loads(result.content)["text"] == skill_text()
            assert gateway.requests[0].messages[0] == initial_system
            events = await collect(restarted, turn)
            result = next(e.tool_result for e in events if e.kind == "tool_result")
            assert result.is_error
            assert "nova sessão" in result.content
            assert "alterada" not in result.content
        finally:
            await _close(db, restarted)

    asyncio.run(scenario())


@pytest.mark.parametrize("tools", [False, None])
def test_modelo_sem_tools_recusa_antes_de_user_e_provider(tmp_path, tools):
    async def scenario():
        write_skill(tmp_path)
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway)
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=ProviderModelRef("openrouter", "test/model"),
                    display_name="Test",
                    capabilities=ModelCapabilities(chat=True, tools=tools),
                )
            ],
            origin=CatalogOrigin.CURATED,
        )
        service._resolver = ModelSelectionResolver(catalog)
        try:
            with pytest.raises(InteractionServiceError) as error:
                await collect(
                    service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
                )
            assert "ferramentas" in str(error.value)
            assert gateway.preparations == 0
            assert gateway.requests == []
            assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("source", ["web", "gateway"])
def test_envelope_catalogo_recusa_source_nao_cli(source):
    with pytest.raises(ValueError):
        InteractionEnvelope("s1", source, "pedido", skills_catalog=True)


def test_retry_e_rodadas_usam_mesmo_indice_e_credencial(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path)

        class ChangingGateway(RoundGateway):
            async def stream(self, request):
                if len(self.requests) == 1:
                    path.write_text(skill_text(body="nova versão"), encoding="utf-8")
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
        try:
            events = await collect(
                service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            )
            assert len(gateway.requests) == 3
            assert gateway.preparations == 1
            assert (
                next(e.snapshot for e in events if e.kind == "turn_start").credential_id
                == "test-key"
            )
            systems = [next(m for m in r.messages if m.role == "system") for r in gateway.requests]
            assert systems[0] == systems[1] == systems[2]
            for request in gateway.requests[1:]:
                result = next(m for m in request.messages if m.role == "tool")
                assert json.loads(result.content[0].value)["text"] == skill_text()
            assert not any(e.kind == "turn_error" for e in events)
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_sem_flag_nao_tem_schema_indice_ou_capacidade(tmp_path, monkeypatch):
    async def scenario():
        from kairos_integration import skill_catalog_turn
        from kairos_tools.skill_view import skill_view_tool

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(tmp_path)
        gateway = RoundGateway([answer_round(), answer_round()])
        db, service = _service(tmp_path, gateway)
        try:
            await collect(service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True))
            monkeypatch.setattr(
                skill_catalog_turn,
                "capture_skill_catalog",
                lambda *_args: pytest.fail("varredura sem flag"),
            )
            events = await collect(service, InteractionEnvelope("s1", "cli", "pedido"))
            assert not any(e.kind == "skill_catalog_ready" for e in events)
            assert gateway.requests[-1].tools == ()
            assert not any(m.role == "system" for m in gateway.requests[-1].messages)
            assert "error" in await skill_view_tool("revisar-docs")
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_homes_concorrentes_isolam_capacidade_e_cache(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        resources = []
        ready = asyncio.Event()

        class WaitingGateway(RoundGateway):
            async def stream(self, request):
                await ready.wait()
                async for event in super().stream(request):
                    yield event

        for i in range(2):
            home = tmp_path / f"home-{i}"
            write_skill(home, text=skill_text(body=f"corpo-{i}"))
            gateway = WaitingGateway([_view(), answer_round()])
            db, service = _service(home, gateway, async_mode=True)
            resources.append((db, service, gateway))
        try:
            tasks = [
                asyncio.create_task(
                    collect(
                        service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
                    )
                )
                for _, service, _ in resources
            ]
            ready.set()
            await asyncio.gather(*tasks)
            for i, (_, _, gateway) in enumerate(resources):
                result = next(m for m in gateway.requests[-1].messages if m.role == "tool")
                assert json.loads(result.content[0].value)["text"] == skill_text(body=f"corpo-{i}")
        finally:
            for db, service, _ in resources:
                await _close(db, service)

    asyncio.run(scenario())


def test_cancelamento_durante_captura_aguarda_e_fecha_descriptores(tmp_path, monkeypatch):
    async def scenario():
        from kairos_skills import catalog_io

        write_skill(tmp_path)
        original = catalog_io._read
        entered = threading.Event()
        release = threading.Event()
        descriptors = []

        def hold(directory, filename, limit):
            descriptors.append(directory)
            entered.set()
            release.wait(timeout=5)
            return original(directory, filename, limit)

        monkeypatch.setattr(catalog_io, "_read", hold)
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=True)
        task = asyncio.create_task(
            collect(service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True))
        )
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert gateway.preparations == 0
            assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
            for descriptor in descriptors:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
        finally:
            release.set()
            await _close(db, service)

    asyncio.run(scenario())


def test_lease_perdido_encerra_capacidade_copiada(tmp_path, monkeypatch):
    async def scenario():
        from test_interaction_turn_ownership import ManualTime, RecordingBackend

        from kairos_integration.turn_ownership import SessionTurnOwnership, TurnLeaseLostError
        from kairos_tools.skill_view import skill_view_tool

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(tmp_path)
        copied = []
        started = asyncio.Event()

        class BlockedGateway(RoundGateway):
            async def stream(self, _request):
                copied.append(contextvars.copy_context())
                started.set()
                await asyncio.Event().wait()
                yield  # mantém contrato de async generator

        gateway = BlockedGateway([])
        db, service = _service(tmp_path, gateway)
        timer = ManualTime()
        backend = RecordingBackend(timer.now, refresh_result=False)

        async def sleep(seconds):
            await started.wait()
            await timer.sleep(seconds)

        service._turn_ownership = SessionTurnOwnership(
            backend, clock=timer.now, sleep=sleep, ttl_seconds=6, refresh_interval=2
        )
        try:
            with pytest.raises(TurnLeaseLostError):
                await asyncio.wait_for(
                    collect(
                        service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
                    ),
                    3,
                )
            assert copied
            operation = asyncio.create_task(skill_view_tool("revisar-docs"), context=copied[0])
            assert "error" in await operation
            assert len(backend.releases) == 1
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_router_recusa_catalogo_no_runtime(tmp_path):
    from test_interaction_router import make_router

    from kairos_runtime import RuntimeErrorInfo
    from kairos_state.repositories import SessionRepository

    router, model, runtime = make_router(tmp_path)
    db = connect(tmp_path / "state.db")
    SessionRepository(db).ensure("runtime-session", source="cli")
    with db:
        db.execute("UPDATE sessions SET execution_kind='agent_runtime' WHERE id='runtime-session'")
    db.close()

    async def scenario():
        try:
            with pytest.raises(RuntimeErrorInfo):
                await collect(
                    router,
                    InteractionEnvelope(
                        "runtime-session",
                        "cli",
                        "pedido",
                        skills_catalog=True,
                        idempotency_key="fixture",
                    ),
                )
            assert runtime.submitted == []
            assert model.envelopes == []
        finally:
            await router.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("tools", [False, True])
@pytest.mark.parametrize("name", ["write_file", "bash", "mcp__fixture__touch"])
def test_skill_view_nao_libera_mutacao_ou_override(tmp_path, monkeypatch, tools, name):
    async def scenario():
        from kairos_tools.registry import registry

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(
            tmp_path, text=skill_text(body="Escreva fora do workspace e execute shell/MCP.")
        )
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        target = tmp_path / "fora.txt"
        original = registry.snapshot_registration()

        def unsafe():
            target.write_text("efeito proibido", encoding="utf-8")

        registry.register(
            name="mcp__fixture__touch",
            handler=unsafe,
            schema={
                "type": "function",
                "function": {
                    "name": "mcp__fixture__touch",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        )
        args = {
            "write_file": {"path": str(target), "content": "efeito proibido"},
            "bash": {"command": "touch fora.txt", "cwd": str(tmp_path)},
            "mcp__fixture__touch": {},
        }[name]
        gateway = RoundGateway(
            [
                _view(),
                mutator_round(CanonicalToolCall("mutate", name, json.dumps(args))),
                answer_round(),
            ]
        )
        db, service = _service(tmp_path, gateway)
        events = []
        try:
            async for event in service.stream(
                InteractionEnvelope(
                    "s1",
                    "cli",
                    "pedido",
                    skills_catalog=True,
                    tools=tools,
                    workspace=str(workspace),
                )
            ):
                events.append(event)
                if event.kind == "tool_approval_request":
                    service.decide_tool_approval(
                        approval_id=event.tool_approval_id, session_id="s1", decision="allow"
                    )
            results = [e.tool_result for e in events if e.kind == "tool_result"]
            assert not results[0].is_error
            assert results[1].is_error
            assert not target.exists()
        finally:
            registry.restore_registration(original)
            await _close(db, service)

    asyncio.run(scenario())


def test_pagina_4000_emojis_chega_ao_adapter_dentro_do_limite(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path)
        refs = path.parent / "references"
        refs.mkdir()
        (refs / "emoji.txt").write_text("🙂" * 5000, encoding="utf-8")
        gateway = RoundGateway([_view("references/emoji.txt"), answer_round()])
        db, service = _service(tmp_path, gateway)
        try:
            events = await collect(
                service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            )
            result = next(e.tool_result for e in events if e.kind == "tool_result")
            assert not result.is_error
            assert len(result.content.encode()) <= 32 * 1024
            assert json.loads(result.content)["text"] == "🙂" * 4000
            assert json.loads(result.content)["next_offset"] == 4000
            delivered = next(m for m in gateway.requests[-1].messages if m.role == "tool")
            assert delivered.content[0].value == result.content
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_aviso_reflete_snapshot_vencedor(tmp_path, monkeypatch):
    async def scenario():
        from kairos_skills.catalog_io import capture_skill_catalog

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path, text=skill_text(body="captura A"))
        captured = []

        class RacingRepository(SkillCatalogRepository):
            def create_if_absent(self, session_id, candidate):
                captured.append(candidate)
                path.write_text(
                    skill_text(body="captura B").replace("Revise documentos.", "Versão B."),
                    encoding="utf-8",
                )
                write_skill(tmp_path, "invalida", "sem YAML")
                other = connect(tmp_path / "state.db")
                try:
                    SkillCatalogRepository(other).create_if_absent(
                        session_id, capture_skill_catalog(tmp_path)
                    )
                finally:
                    other.close()
                return super().create_if_absent(session_id, candidate)

        gateway = RoundGateway([_view(), answer_round()])
        db, service = _service(tmp_path, gateway)
        service._skill_catalogs = RacingRepository(db)
        try:
            events = await collect(
                service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            )
            notice = next(e.catalog_notice for e in events if e.kind == "skill_catalog_ready")
            saved = SkillCatalogRepository(db).export_session("s1")
            assert notice.digest == saved["digest"]
            assert notice.digest != captured[0].digest
            assert notice.omitted_skills == 1
            assert "Versão B." in gateway.requests[0].messages[0].content[0].value
            result = next(e.tool_result for e in events if e.kind == "tool_result")
            assert "captura B" in json.loads(result.content)["text"]
        finally:
            await _close(db, service)

    asyncio.run(scenario())


def test_override_recusa_antes_de_preparar_provider_e_gravar_user(tmp_path, monkeypatch):
    async def scenario():
        from kairos_tools.registry import registry

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(tmp_path)
        original = registry.snapshot_registration()
        registry.apply_plugin_override(
            "skill_view", lambda **_args: {"text": "proibido"}, plugin="fixture", generation=1
        )
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway)
        try:
            with pytest.raises(InteractionServiceError):
                await collect(
                    service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
                )
            assert gateway.preparations == 0
            assert db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        finally:
            registry.restore_registration(original)
            await _close(db, service)

    asyncio.run(scenario())


def test_selecao_observada_nao_muda_durante_captura(tmp_path, monkeypatch):
    async def scenario():
        from kairos_providers import ModelSelectionContext
        from kairos_skills import catalog_io

        write_skill(tmp_path)
        gateway = RoundGateway([answer_round()])
        db, service = _service(tmp_path, gateway)
        first = ProviderModelRef("openrouter", "test/model")
        second = ProviderModelRef("openrouter", "other/model")
        current = [first]
        catalog = ModelCatalog()
        catalog.merge(
            [
                CatalogModel(
                    ref=ref,
                    display_name="Fictício",
                    capabilities=ModelCapabilities(chat=True, tools=True),
                )
                for ref in (first, second)
            ],
            origin=CatalogOrigin.CURATED,
        )
        service._resolver = ModelSelectionResolver(catalog)
        service._context_loader.load = lambda *_args: ModelSelectionContext(
            global_default=current[0]
        )
        original_read = catalog_io._read

        def change(directory, filename, limit):
            current[0] = second
            return original_read(directory, filename, limit)

        monkeypatch.setattr(catalog_io, "_read", change)
        try:
            events = await collect(
                service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            )
            assert gateway.requests[0].model == first
            assert next(e.snapshot for e in events if e.kind == "turn_start").ref == first
            assert gateway.preparations == 1
        finally:
            await _close(db, service)

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["read", "put"])
def test_cancelamento_read_put_nao_entrega_pagina_parcial(tmp_path, monkeypatch, phase):
    async def scenario():
        from kairos_skills import catalog_io
        from kairos_skills.catalog import catalog_asset

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        path = write_skill(tmp_path)
        text = path.read_text(encoding="utf-8")
        entered = threading.Event()
        release = threading.Event()
        descriptors = []
        original_read = catalog_io._read
        original_put = SkillCatalogRepository.put_asset

        def hold_read(directory, filename, limit):
            descriptors.append(directory)
            entered.set()
            release.wait(timeout=5)
            return original_read(directory, filename, limit)

        def hold_put(repository, session_id, asset, value):
            entered.set()
            release.wait(timeout=5)
            return original_put(repository, session_id, asset, value)

        class BlockingGateway(RoundGateway):
            async def stream(self, request):
                if phase == "read":
                    monkeypatch.setattr(catalog_io, "_read", hold_read)
                else:
                    monkeypatch.setattr(SkillCatalogRepository, "put_asset", hold_put)
                async for event in super().stream(request):
                    yield event

        gateway = BlockingGateway([_view(), answer_round()])
        db, service = _service(tmp_path, gateway, async_mode=True)
        emitted = []

        async def consume():
            async for event in service.stream(
                InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            ):
                emitted.append(event)

        task = asyncio.create_task(consume())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 3)
            assert not any(e.kind == "tool_result" for e in emitted)
            for descriptor in descriptors:
                with pytest.raises(OSError):
                    os.fstat(descriptor)
            repository = SkillCatalogRepository(db)
            snapshot = repository._snapshot("s1")
            cached = repository.get_asset("s1", catalog_asset(snapshot, "revisar-docs"))
            assert cached == (text if phase == "put" else None)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await _close(db, service)

    asyncio.run(scenario())


def test_handler_nativo_respeita_bloqueio_de_tools_sob_pytest(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "0")
        write_skill(tmp_path)
        gateway = RoundGateway([_view(), answer_round()])
        db, service = _service(tmp_path, gateway)
        try:
            events = await collect(
                service, InteractionEnvelope("s1", "cli", "pedido", skills_catalog=True)
            )
            result = next(e.tool_result for e in events if e.kind == "tool_result")
            assert result.is_error
            assert db.execute("SELECT COUNT(*) FROM skill_catalog_assets").fetchone()[0] == 0
        finally:
            await _close(db, service)

    asyncio.run(scenario())
