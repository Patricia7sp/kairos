import asyncio
import sqlite3
import threading
from pathlib import Path
from typing import get_type_hints

import pytest

from kairos_integration import (
    ComposedInteractionService,
    build_interaction_service,
    composition,
)
from kairos_integration.admission import AdmissionState
from kairos_integration.interaction_contract import (
    InteractionEnvelope,
    InteractionServiceUnavailableError,
)
from kairos_providers import (
    CatalogOrigin,
    ModelCatalog,
    ProviderEvent,
    ProviderModelRef,
    ResolvedModelSelection,
    SelectionReason,
    TokenUsage,
    curated_models,
)
from kairos_providers.gateway import ProviderGateway


class StreamingAdapter:
    async def stream(self, _request):
        yield ProviderEvent(
            kind="usage",
            usage=TokenUsage(input_tokens=7, output_tokens=3),
        )
        yield ProviderEvent(kind="finish", finish_reason="stop")


class StreamingGateway:
    def __init__(self):
        self.catalog = ModelCatalog()
        self.catalog.merge(curated_models(), origin=CatalogOrigin.CURATED)
        self.closed = False
        self.close_attempts = 0

    def create_adapter(self, _ref):
        return StreamingAdapter()

    async def aclose(self):
        self.close_attempts += 1
        self.closed = True


class BackpressuredAdapter:
    def __init__(self) -> None:
        self.cleanup_complete = False

    async def stream(self, _request):
        try:
            yield ProviderEvent(kind="text_delta", text="parcial")
            await asyncio.Event().wait()
        finally:
            self.cleanup_complete = True


class BackpressuredGateway(StreamingGateway):
    def __init__(self) -> None:
        super().__init__()
        self.adapter = BackpressuredAdapter()
        self.cleanup_complete = False

    def create_adapter(self, _ref):
        return self.adapter

    async def aclose(self):
        assert self.adapter.cleanup_complete
        await super().aclose()
        self.cleanup_complete = True


def test_composition_usa_gateway_e_state_compartilhados(tmp_path):
    """Trocar o home ou não compartilhar o gateway quebraria o runtime composto."""
    service = build_interaction_service(tmp_path)

    assert isinstance(service.gateway, ProviderGateway)
    assert service.home == tmp_path
    asyncio.run(service.aclose())


def test_builder_declara_tipo_publico_com_lifecycle(tmp_path):
    """Apagar o tipo composto da assinatura esconde o fechamento de Web e CLI."""
    service = build_interaction_service(tmp_path)

    assert get_type_hints(build_interaction_service)["return"] is ComposedInteractionService
    assert isinstance(service, ComposedInteractionService)
    assert callable(service.aclose)

    asyncio.run(service.aclose())


def test_composition_le_config_e_state_somente_do_home_fornecido(tmp_path, monkeypatch):
    """Consultar KAIROS_HOME global misturaria perfil, catálogo e transcript."""
    supplied_home = tmp_path / "supplied"
    environment_home = tmp_path / "environment"
    supplied_home.mkdir()
    environment_home.mkdir()
    (supplied_home / "config.yaml").write_text(
        "provider: gemini\nmodel: gemini-2.0-flash\n",
        encoding="utf-8",
    )
    (environment_home / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KAIROS_HOME", str(environment_home))

    service = build_interaction_service(supplied_home)
    snapshot = service._resolve_snapshot(
        InteractionEnvelope(conversation_id="s1", source="test", content="olá")
    )

    assert snapshot.ref == ProviderModelRef("gemini", "gemini-2.0-flash")
    assert (supplied_home / "state.db").exists()
    assert not (environment_home / "state.db").exists()
    asyncio.run(service.aclose())


def test_composition_fecha_somente_os_recursos_que_criou(tmp_path, monkeypatch):
    """Omitir gateway ou banco do fechamento vazaria recursos entre superfícies."""

    class ClosingGateway:
        def __init__(self):
            self.catalog = ModelCatalog()
            self.closed = False

        async def aclose(self):
            self.closed = True

    gateway = ClosingGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)

    async def close_twice():
        async with service as entered:
            assert entered is service
        await service.aclose()

    asyncio.run(close_twice())

    assert gateway.closed
    envelope = InteractionEnvelope(
        conversation_id="s1",
        source="test",
        content="olá",
    )
    with pytest.raises(InteractionServiceUnavailableError):
        asyncio.run(anext(service.stream(envelope)))


def test_aclose_persiste_uso_enfileirado_por_stream(tmp_path, monkeypatch):
    """Fechar o banco sem drenar a fila perde os contadores do último turno."""
    (tmp_path / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    gateway = StreamingGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)

    async def stream_and_close():
        events = [
            event
            async for event in service.stream(
                InteractionEnvelope(conversation_id="s1", source="test", content="olá")
            )
        ]
        assert events[-1].kind == "turn_end"
        await service.aclose()

    asyncio.run(stream_and_close())

    with sqlite3.connect(tmp_path / "state.db") as connection:
        row = connection.execute(
            "SELECT api_call_count, input_tokens, output_tokens FROM session_model_usage"
        ).fetchone()
    assert row == (1, 7, 3)
    assert gateway.closed


def test_falha_de_construcao_pos_gateway_fecha_gateway_e_banco(tmp_path, monkeypatch):
    """Falhar após transferir ownership não pode vazar nenhum recurso já criado."""
    gateway = StreamingGateway()
    connections = []
    real_connect = composition.connect

    def tracking_connect(path):
        connection = real_connect(path)
        connections.append(connection)
        return connection

    def fail_construction(**_kwargs):
        raise RuntimeError("falha depois do gateway")

    monkeypatch.setattr(composition, "connect", tracking_connect)
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    monkeypatch.setattr(composition, "ComposedInteractionService", fail_construction)

    async def build_inside_running_loop():
        with pytest.raises(RuntimeError, match="falha depois do gateway"):
            build_interaction_service(tmp_path)

    asyncio.run(build_inside_running_loop())

    assert gateway.closed
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        connections[0].execute("SELECT 1")


def test_later_turn_drains_batch_restored_by_failed_terminal_flush(tmp_path, monkeypatch):
    """A falha terminal preserva o lote para o turno seguinte drená-lo junto ao novo uso."""
    (tmp_path / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    gateway = StreamingGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    real_flush = service._usage.flush
    flush_attempts = 0

    def flaky_flush():
        nonlocal flush_attempts
        flush_attempts += 1
        if flush_attempts == 1:
            raise RuntimeError("flush indisponível")
        return real_flush()

    service._usage.flush = flaky_flush

    async def stream_retry_and_close():
        first = [
            event
            async for event in service.stream(
                InteractionEnvelope(conversation_id="s1", source="test", content="olá")
            )
        ]
        assert first[-1].error_kind == "persistence"
        assert service._usage.pending_count() == 1
        with sqlite3.connect(tmp_path / "state.db") as connection:
            assert connection.execute("SELECT count(*) FROM session_model_usage").fetchone()[0] == 0

        second = [
            event
            async for event in service.stream(
                InteractionEnvelope(conversation_id="s2", source="test", content="de novo")
            )
        ]
        assert second[-1].kind == "turn_end"
        assert service._usage.pending_count() == 0
        await service.aclose()

    asyncio.run(stream_retry_and_close())

    assert flush_attempts == 3
    assert gateway.close_attempts == 1
    with sqlite3.connect(tmp_path / "state.db") as connection:
        rows = connection.execute(
            "SELECT session_id, api_call_count, input_tokens, output_tokens "
            "FROM session_model_usage ORDER BY session_id"
        ).fetchall()
    assert rows == [("s1", 1, 7, 3), ("s2", 1, 7, 3)]
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        service._connection.execute("SELECT 1")


def test_aclose_concorrente_compartilha_um_cleanup(tmp_path, monkeypatch):
    """Dois callers sobrepostos não podem duplicar flush, gateway ou DB close."""

    class BarrierGateway(StreamingGateway):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def aclose(self):
            self.close_attempts += 1
            self.started.set()
            await self.release.wait()
            self.closed = True

    gateway = BarrierGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    real_flush = service._usage.flush
    flush_attempts = 0

    def counting_flush():
        nonlocal flush_attempts
        flush_attempts += 1
        return real_flush()

    service._usage.flush = counting_flush

    async def close_together():
        start = asyncio.Event()

        async def close_after_barrier():
            await start.wait()
            await service.aclose()

        callers = [asyncio.create_task(close_after_barrier()) for _ in range(2)]
        start.set()
        await gateway.started.wait()
        await asyncio.sleep(0)
        attempts_during_overlap = (flush_attempts, gateway.close_attempts)
        gateway.release.set()
        results = await asyncio.gather(*callers, return_exceptions=True)
        assert attempts_during_overlap == (1, 1)
        assert results == [None, None]

    asyncio.run(close_together())

    assert flush_attempts == 1
    assert gateway.close_attempts == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        service._connection.execute("SELECT 1")


def test_cancelar_um_caller_nao_cancela_cleanup_compartilhado(tmp_path, monkeypatch):
    """Cancelar o primeiro waiter não pode deixar o segundo preso nem repetir cleanup."""

    class BarrierGateway(StreamingGateway):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def aclose(self):
            self.close_attempts += 1
            self.started.set()
            await self.release.wait()
            self.closed = True

    gateway = BarrierGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    real_flush = service._usage.flush
    flush_attempts = 0

    def counting_flush():
        nonlocal flush_attempts
        flush_attempts += 1
        return real_flush()

    service._usage.flush = counting_flush

    async def cancel_leader():
        leader = asyncio.create_task(service.aclose())
        await gateway.started.wait()
        waiter = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        leader.cancel()
        leader_result = (await asyncio.gather(leader, return_exceptions=True))[0]
        gateway.release.set()
        waiter_result = (
            await asyncio.wait_for(asyncio.gather(waiter, return_exceptions=True), timeout=1)
        )[0]
        assert isinstance(leader_result, asyncio.CancelledError)
        assert waiter_result is None

    asyncio.run(cancel_leader())

    assert flush_attempts == 1
    assert gateway.close_attempts == 1


class ThreadSafeUsage:
    def __init__(self):
        self.flush_attempts = 0
        self._lock = threading.Lock()

    def flush(self):
        with self._lock:
            self.flush_attempts += 1
        return 0


class ThreadSafeConnection:
    def __init__(self):
        self.close_attempts = 0
        self._lock = threading.Lock()

    def close(self):
        with self._lock:
            self.close_attempts += 1


class ThreadBarrierGateway:
    def __init__(self):
        self.close_attempts = 0
        self.close_thread_id = None
        self.started = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()

    async def aclose(self):
        with self._lock:
            self.close_attempts += 1
            self.close_thread_id = threading.get_ident()
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.001)


def thread_safe_service():
    gateway = ThreadBarrierGateway()
    usage = ThreadSafeUsage()
    connection = ThreadSafeConnection()
    service = ComposedInteractionService(
        home=Path("/thread-test"),
        connection=connection,
        gateway=gateway,
        resolver=object(),
        context_loader=object(),
        sessions=object(),
        messages=object(),
        usage=usage,
    )
    return service, gateway, usage, connection


def run_close_in_thread(
    service, results: dict[str, object], name: str, joined: threading.Event | None = None
):
    async def close():
        task = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        if joined is not None:
            joined.set()
        await task

    try:
        results[name] = asyncio.run(close())
    except BaseException as exc:  # noqa: BLE001 - captura CancelledError do thread waiter
        results[name] = exc


def test_service_aclose_em_loops_de_threads_compartilha_tentativa():
    """Loops distintos devem observar um cleanup global, sem Future estrangeira."""
    service, gateway, usage, connection = thread_safe_service()
    results: dict[str, object] = {}
    second_joined = threading.Event()

    first = threading.Thread(
        target=run_close_in_thread,
        args=(service, results, "first"),
        daemon=True,
    )
    first.start()
    assert gateway.started.wait(timeout=2)
    second = threading.Thread(
        target=run_close_in_thread,
        args=(service, results, "second", second_joined),
        daemon=True,
    )
    second.start()
    assert second_joined.wait(timeout=2)
    gateway.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == {"first": None, "second": None}
    assert usage.flush_attempts == 1
    assert gateway.close_attempts == 1
    assert connection.close_attempts == 1


def test_active_turn_release_wakes_foreign_loop_drain_thread_safely():
    """Loop-affine drain notification can fail the turn and strand cross-thread close."""
    service, gateway, usage, connection = thread_safe_service()
    gateway.release.set()
    turn_active, release_turn, close_reserved = (threading.Event() for _ in range(3))
    close_loop: list[asyncio.AbstractEventLoop] = []
    results: dict[str, object] = {}

    def run_turn() -> None:
        async def hold_admitted_turn() -> None:
            assert service._admission is not None
            async with service._admission.admit():
                turn_active.set()
                while not release_turn.is_set():
                    await asyncio.sleep(0.001)

        try:
            results["turn"] = asyncio.run(hold_admitted_turn(), debug=True)
        except BaseException as exc:  # noqa: BLE001 - outcome cross-loop sob teste
            results["turn"] = exc

    def run_foreign_close() -> None:
        async def close_after_reservation() -> None:
            close_loop.append(asyncio.get_running_loop())
            close_task = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            close_reserved.set()
            await close_task

        try:
            results["close"] = asyncio.run(close_after_reservation(), debug=True)
        except BaseException as exc:  # noqa: BLE001 - outcome cross-loop sob teste
            results["close"] = exc

    turn, close = (
        threading.Thread(target=run_turn, daemon=True),
        threading.Thread(target=run_foreign_close, daemon=True),
    )
    turn.start()
    assert turn_active.wait(timeout=2), results
    close.start()
    assert close_reserved.wait(timeout=2)
    assert close.is_alive(), "close estrangeiro deve aguardar o turno admitido"

    release_turn.set()
    for thread in (turn, close):
        thread.join(timeout=2)
    close_stranded = close.is_alive()
    if close_stranded:

        def cancel_foreign_tasks() -> None:
            for task in asyncio.all_tasks(close_loop[0]):
                task.cancel()

        close_loop[0].call_soon_threadsafe(cancel_foreign_tasks)
        close.join(timeout=2)
    if results.get("close") is not None:
        asyncio.run(service.aclose())

    assert not turn.is_alive()
    assert not close_stranded
    assert results == {"turn": None, "close": None}
    assert gateway.close_attempts == 1
    assert usage.flush_attempts == 1
    assert connection.close_attempts == 1


def test_cancelar_waiter_em_outro_loop_nao_cancela_cleanup_global():
    """Cancelamento loop-local não pode cancelar o attempt usado por outro loop."""
    service, gateway, usage, connection = thread_safe_service()
    results: dict[str, object] = {}

    leader = threading.Thread(
        target=run_close_in_thread,
        args=(service, results, "leader"),
        daemon=True,
    )
    leader.start()
    assert gateway.started.wait(timeout=2)

    def run_cancelled_waiter() -> None:
        async def cancel_waiter():
            task = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            task.cancel()
            return (await asyncio.gather(task, return_exceptions=True))[0]

        results["cancelled"] = asyncio.run(cancel_waiter())

    cancelled = threading.Thread(target=run_cancelled_waiter, daemon=True)
    cancelled.start()
    cancelled.join(timeout=2)
    assert not cancelled.is_alive()
    gateway.release.set()
    leader.join(timeout=2)

    assert not leader.is_alive()
    assert results["leader"] is None
    assert isinstance(results["cancelled"], asyncio.CancelledError)
    assert usage.flush_attempts == 1
    assert gateway.close_attempts == 1
    assert connection.close_attempts == 1


def test_shutdown_real_do_asyncio_run_drena_cleanup_no_loop_proprietario():
    """Runner drena o close pre-body no loop afim e um waiter estrangeiro o observa."""
    service, gateway, usage, connection = thread_safe_service()
    results: dict[str, object] = {}
    foreign_joined = threading.Event()

    def run_owner() -> None:
        results["owner_thread_id"] = threading.get_ident()

        async def launch_and_return() -> None:
            waiter = asyncio.create_task(service.aclose())
            await asyncio.sleep(0)
            results["started_before_shutdown"] = gateway.started.is_set()
            results["waiter_done_before_shutdown"] = waiter.done()

        results["owner"] = asyncio.run(launch_and_return())

    def run_foreign_waiter() -> None:
        async def wait_for_close() -> None:
            foreign_joined.set()
            await service.aclose()

        try:
            results["foreign"] = asyncio.run(wait_for_close())
        except BaseException as exc:  # noqa: BLE001 - outcome cross-loop sob teste
            results["foreign"] = exc

    owner = threading.Thread(target=run_owner, daemon=True)
    owner.start()
    assert gateway.started.wait(timeout=2)
    assert results["started_before_shutdown"] is False
    assert results["waiter_done_before_shutdown"] is False
    assert owner.is_alive(), "asyncio.run deveria aguardar o cleanup iniciado no shutdown"

    foreign = threading.Thread(target=run_foreign_waiter, daemon=True)
    foreign.start()
    assert foreign_joined.wait(timeout=2)
    gateway.release.set()
    owner.join(timeout=2)
    foreign.join(timeout=2)

    assert not owner.is_alive()
    assert not foreign.is_alive()
    assert results["owner"] is None
    assert results["foreign"] is None
    assert gateway.close_thread_id == results["owner_thread_id"]
    assert usage.flush_attempts == 1
    assert gateway.close_attempts == 1
    assert connection.close_attempts == 1


def test_falha_ao_agendar_cleanup_publica_resultado_e_permite_retry(monkeypatch):
    """Falha de launch não pode deixar um outcome pendente nem duplicar cleanup."""
    from kairos_providers import _async_cleanup

    service, gateway, usage, connection = thread_safe_service()

    with monkeypatch.context() as patch_context:
        patch_context.setattr(
            _async_cleanup,
            "_launch_cleanup_task",
            lambda _coroutine, *, name: (_ for _ in ()).throw(RuntimeError(name)),
        )
        with pytest.raises(RuntimeError, match="kairos-interaction-service-close"):
            asyncio.run(service.aclose())

    gateway.release.set()
    asyncio.run(asyncio.wait_for(service.aclose(), timeout=1))

    assert usage.flush_attempts == 1
    assert gateway.close_attempts == 1
    assert connection.close_attempts == 1


def test_cleanup_nao_usa_task_factory_eager_do_caller():
    """Uma factory customizada não pode executar cleanup durante a reserva bloqueada."""
    service, gateway, usage, connection = thread_safe_service()
    gateway.release.set()

    async def close_with_hostile_factory() -> None:
        loop = asyncio.get_running_loop()

        def hostile_factory(_loop, coroutine, **_kwargs):
            coroutine.close()
            raise AssertionError("task factory do caller executou cleanup")

        loop.set_task_factory(hostile_factory)
        try:
            await service.aclose()
        finally:
            loop.set_task_factory(None)

    asyncio.run(close_with_hostile_factory())

    assert usage.flush_attempts == 1
    assert gateway.close_attempts == 1
    assert connection.close_attempts == 1


@pytest.mark.anyio
async def test_composed_close_waits_for_backpressured_turn_before_resources(tmp_path, monkeypatch):
    """Dropping admission before public aclose would close owned resources under the turn."""
    (tmp_path / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    gateway = BackpressuredGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    stream = service.stream(
        InteractionEnvelope(conversation_id="active", source="test", content="olá")
    )
    close_task = None
    try:
        assert (await anext(stream)).kind == "turn_start"
        assert (await anext(stream)).kind == "delta"
        close_task = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)

        assert not close_task.done()
        with pytest.raises(InteractionServiceUnavailableError):
            await anext(
                service.stream(
                    InteractionEnvelope(
                        conversation_id="later",
                        source="test",
                        content="depois",
                    )
                )
            )

        await stream.aclose()
        await close_task

        assert gateway.cleanup_complete
        assert service._persistence is not None
        assert service._persistence._connection is None
    finally:
        await stream.aclose()
        if close_task is not None:
            await asyncio.gather(close_task, return_exceptions=True)
        await asyncio.gather(service.aclose(), return_exceptions=True)


@pytest.mark.anyio
async def test_cancelled_close_waiter_does_not_cancel_shared_drain(tmp_path, monkeypatch):
    """Forwarding waiter cancellation into the reserved close task strands later waiters."""
    (tmp_path / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    gateway = BackpressuredGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    stream = service.stream(
        InteractionEnvelope(conversation_id="active", source="test", content="olá")
    )
    first = None
    second = None
    try:
        assert (await anext(stream)).kind == "turn_start"
        assert (await anext(stream)).kind == "delta"
        first = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        second = asyncio.create_task(service.aclose())
        await asyncio.sleep(0)
        assert not second.done()
        assert service._persistence is not None
        assert service._persistence._connection is not None
        await stream.aclose()
        await second

        assert gateway.close_attempts == 1
        assert gateway.cleanup_complete
    finally:
        await stream.aclose()
        for task in (first, second):
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
        await asyncio.gather(service.aclose(), return_exceptions=True)


@pytest.mark.anyio
async def test_failed_close_retry_keeps_admission_draining():
    """A failed resource close must not reopen turns before a later successful retry."""

    class RetryGateway:
        def __init__(self) -> None:
            self.close_attempts = 0

        async def aclose(self) -> None:
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise RuntimeError("database-internal")

    gateway = RetryGateway()
    usage = ThreadSafeUsage()
    connection = ThreadSafeConnection()
    service = ComposedInteractionService(
        home=Path("/retry-test"),
        connection=connection,
        gateway=gateway,
        resolver=object(),
        context_loader=object(),
        sessions=object(),
        messages=object(),
        usage=usage,
    )

    with pytest.raises(BaseExceptionGroup, match="falha ao fechar InteractionService"):
        await service.aclose()
    with pytest.raises(InteractionServiceUnavailableError):
        await anext(
            service.stream(
                InteractionEnvelope(conversation_id="later", source="test", content="oi")
            )
        )

    await service.aclose()

    assert gateway.close_attempts == 2


@pytest.mark.anyio
async def test_final_usage_flush_failure_keeps_persistence_open_and_retries(tmp_path, monkeypatch):
    """Fechar o worker após um flush falho tornaria o lote irrecuperável no retry."""
    gateway = StreamingGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)
    assert service._persistence is not None
    await service._persistence.ensure("pending", source="test")
    service._usage.record_event(
        "pending",
        ResolvedModelSelection(
            ProviderModelRef("openai", "gpt-4o"),
            SelectionReason.GLOBAL_DEFAULT,
        ),
        billing_provider="openai",
        billing_base_url="https://api.openai.com/v1",
        billing_mode="api_key",
        usage=TokenUsage(input_tokens=2, output_tokens=1),
        api_call_count=1,
    )
    real_flush = service._usage.flush
    flush_attempts = 0

    def fail_final_flush_once():
        nonlocal flush_attempts
        flush_attempts += 1
        if flush_attempts == 1:
            raise RuntimeError("database credential detail")
        return real_flush()

    service._usage.flush = fail_final_flush_once

    with pytest.raises(BaseExceptionGroup, match="falha ao fechar InteractionService"):
        await service.aclose()

    assert flush_attempts == 1
    assert service._usage.pending_count() == 1
    assert service._persistence._connection is not None
    assert service._connection.execute("SELECT 1").fetchone()[0] == 1
    assert service._admission is not None
    assert service._admission._state is AdmissionState.DRAINING
    with pytest.raises(InteractionServiceUnavailableError):
        await anext(
            service.stream(
                InteractionEnvelope(conversation_id="later", source="test", content="oi")
            )
        )

    await service.aclose()

    assert flush_attempts == 2
    assert service._usage.pending_count() == 0
    assert service._persistence._connection is None
    assert service._admission._state is AdmissionState.CLOSED
    with sqlite3.connect(tmp_path / "state.db") as connection:
        row = connection.execute(
            "SELECT api_call_count, input_tokens, output_tokens "
            "FROM session_model_usage WHERE session_id = 'pending'"
        ).fetchone()
    assert row == (1, 2, 1)
