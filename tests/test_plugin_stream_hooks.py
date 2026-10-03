"""Família stream: observar o stream de plugin sem poder transformá-lo (RF-08).

O critério da spec é literal: um plugin registrado em `on_stream_delta` que tenta
alterar o texto recebido **não** altera o stream entregue ao usuário. Estes
testes afirmam isso de verdade — plugin em disco, evento de verdade — e afirmam
o outro lado do contrato: callback lento ou levantando não segura o token nem
mata o observador. Nada aqui lê código-fonte nem conta itens de catálogo.
"""

import asyncio
import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from kairos_integration import InteractionEnvelope
from kairos_integration.interaction_service import InteractionService
from kairos_plugins import (
    HookEmitter,
    HookRegistry,
    StreamHookDispatcher,
    UnknownHook,
    load_plugins,
)
from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ModelPrice,
    ModelSelectionContext,
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    TokenUsage,
)
from kairos_providers.gateway import ProviderBillingMetadata
from kairos_providers.selection import ModelSelectionResolver
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

# ---------------------------------------------------------------------------
# o despachante, isolado
# ---------------------------------------------------------------------------


@pytest.fixture
def anyio_backend():
    return "asyncio"


def registro_observador(**callbacks) -> HookRegistry:
    registry = HookRegistry()
    for hook, callback in callbacks.items():
        registry.register(hook, callback, plugin="observador")
    return registry


def test_enqueue_sem_ninguem_escuta_nao_custa_nada():
    dispatcher = StreamHookDispatcher()

    assert dispatcher.enabled is False
    assert dispatcher.listening("on_stream_delta") is False
    assert dispatcher.enqueue("on_stream_delta", delta="oi") is False
    assert dispatcher.dropped == 0


@pytest.mark.anyio
async def test_os_dois_modos_de_despacho_nao_se_atravessam():
    """A família stream não entra pelo caminho awaited, e vice-versa."""
    registry = registro_observador(on_stream_delta=lambda **_: None)
    dispatcher = StreamHookDispatcher(registry)
    emitter = HookEmitter(registry)

    with pytest.raises(UnknownHook):
        dispatcher.enqueue("pre_tool_call", tool="bash")
    with pytest.raises(UnknownHook):
        await emitter.emit("on_stream_delta", delta="oi")


@pytest.mark.anyio
async def test_deltas_chegam_em_ordem_com_o_kind_normalizado():
    vistos = []
    dispatcher = StreamHookDispatcher(
        registro_observador(on_stream_delta=lambda **p: vistos.append(p))
    )

    for delta in ("Res", "posta"):
        dispatcher.enqueue("on_stream_delta", delta=delta, kind="text", attempt=1)
    dispatcher.enqueue("on_stream_delta", delta="pensando", kind="reasoning", attempt=1)
    await dispatcher.aclose()

    assert [(v["kind"], v["delta"]) for v in vistos] == [
        ("text", "Res"),
        ("text", "posta"),
        ("reasoning", "pensando"),
    ]


@pytest.mark.anyio
async def test_o_callback_reescreve_o_payload_e_nao_altera_o_item_dos_outros():
    """`callback(**item)` dá um dict ao plugin: reescrever nele não é o item."""
    vistos = []

    def invasor(**payload):
        payload["delta"] = "INVADIDO"
        payload["outro"] = True
        vistos.append(payload)

    def honesto(**payload):
        vistos.append(dict(payload))

    registry = HookRegistry()
    registry.register("on_stream_delta", invasor, plugin="intruso")
    registry.register("on_stream_delta", honesto, plugin="observador")
    dispatcher = StreamHookDispatcher(registry)
    dispatcher.enqueue("on_stream_delta", delta="original", kind="text")
    await dispatcher.aclose()

    # O intruso escreveu "INVADIDO" na cópia dele e o outro plugin, registrado
    # depois, recebeu "original": não existe estado compartilhado para um
    # observador corromper — nem para os que vêm depois dele.
    assert vistos[0]["delta"] == "INVADIDO"
    assert vistos[1] == {"delta": "original", "kind": "text"}


@pytest.mark.anyio
async def test_callback_lento_nao_segura_o_enqueue_e_a_fila_cheia_descarta_o_mais_antigo(caplog):
    started = threading.Event()
    libera = threading.Event()

    def lento(**_):
        started.set()
        libera.wait(timeout=5)

    dispatcher = StreamHookDispatcher(registro_observador(on_stream_delta=lento), queue_size=2)
    with caplog.at_level("WARNING", logger="kairos_plugins.stream_dispatcher"):
        assert dispatcher.enqueue("on_stream_delta", delta="primeiro") is True
        # O worker ainda está preso no primeiro callback: enfileirar os próximos
        # não pode esperar por ele, nem crescer sem limite.
        await asyncio.wait_for(asyncio.to_thread(started.wait, 5), timeout=5)
        for delta in ("segundo", "terceiro", "quarto", "quinto"):
            dispatcher.enqueue("on_stream_delta", delta=delta)

        # O worker segura um item e a fila guarda dois: dos quatro restantes,
        # dois são descartados — e o mais antigo é sempre o que sai.
        assert dispatcher.dropped == 2
        assert any("fila cheia" in r.message for r in caplog.records)
    libera.set()
    await dispatcher.aclose()


@pytest.mark.anyio
async def test_callback_que_levanta_nao_mata_o_worker(caplog):
    vistos = []

    def as_vezes(**payload):
        if payload["delta"] == "ruim":
            raise RuntimeError("plugin quebrado")
        vistos.append(payload["delta"])

    dispatcher = StreamHookDispatcher(registro_observador(on_stream_delta=as_vezes))
    with caplog.at_level("WARNING", logger="kairos_plugins.stream_dispatcher"):
        dispatcher.enqueue("on_stream_delta", delta="ruim")
        dispatcher.enqueue("on_stream_delta", delta="bom")
        await dispatcher.aclose()

    assert vistos == ["bom"]
    assert any("stream preservado" in r.message for r in caplog.records)


@pytest.mark.anyio
async def test_o_dreno_entrega_a_fila_e_o_turno_seguinte_tem_worker_de_novo():
    vistos = []
    dispatcher = StreamHookDispatcher(
        registro_observador(on_stream_delta=lambda **p: vistos.append(p["delta"]))
    )

    dispatcher.enqueue("on_stream_delta", delta="turno-1")
    await dispatcher.aclose()
    dispatcher.enqueue("on_stream_delta", delta="turno-2")
    await dispatcher.aclose()

    assert vistos == ["turno-1", "turno-2"]


# ---------------------------------------------------------------------------
# o runtime: o que o turno enfileira
# ---------------------------------------------------------------------------


class FakeAdapter:
    """Adaptador que devolve os eventos dados, ou levanta o erro dado."""

    def __init__(self, events):
        self._events = events

    def stream(self, request):
        async def generator():
            for event in self._events:
                if isinstance(event, BaseException):
                    raise event
                yield event

        return generator()


class AttemptGateway:
    def __init__(self, adapters):
        self._adapters = iter(adapters)

    def prepare(self, ref):
        return SimpleNamespace(
            credential_id="test-key",
            price=ModelPrice(
                prompt=Decimal("0.001"), completion=Decimal("0.002"), request=Decimal("0.01")
            ),
            cost_source="catalog",
            create_adapter=lambda: next(self._adapters),
            billing=ProviderBillingMetadata(ref.provider, "https://example.test", "api_key"),
        )


class StreamRecorder:
    """Despachante de mentira que grava o que o runtime mandou enfileirar."""

    def __init__(self, *, escutando=True):
        self.vistos: list[tuple[str, dict]] = []
        self.fechado = 0
        self.enabled = True
        self._escutando = escutando

    def listening(self, hook):
        return self._escutando

    def enqueue(self, hook, **payload):
        if not self._escutando:
            return False
        self.vistos.append((hook, payload))
        return True

    async def aclose(self, timeout=2.0):
        self.fechado += 1


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    yield connection
    connection.close()


def make_service(db, adapters, *, streams=None):
    catalog = ModelCatalog()
    catalog.merge(
        [
            CatalogModel(
                ref=ProviderModelRef("openrouter", "test/model"),
                display_name="Test",
                capabilities=ModelCapabilities(chat=True, tools=True),
            )
        ],
        origin=CatalogOrigin.CURATED,
    )
    return InteractionService(
        gateway=AttemptGateway(adapters),
        resolver=ModelSelectionResolver(catalog),
        context_loader=SimpleNamespace(
            load=lambda _: ModelSelectionContext(
                global_default=ProviderModelRef("openrouter", "test/model")
            )
        ),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
        stream_hooks=streams,
    )


def texto_e_fim(texto="Resposta."):
    return [
        ProviderEvent(kind="text_delta", text=texto),
        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=7, output_tokens=4)),
        ProviderEvent(kind="finish", finish_reason="stop"),
    ]


def envelope_de_texto():
    return InteractionEnvelope(
        conversation_id="stream",
        source="web",
        content="Conte",
        parameters={"routing": {"data_collection": "deny"}},
    )


async def roda(db, adapters, streams=None):
    service = make_service(db, adapters, streams=streams)
    return [event async for event in service.stream(envelope_de_texto())]


@pytest.mark.anyio
async def test_o_runtime_enfileira_o_texto_do_turno_e_nao_o_argumento_da_ferramenta(db):
    recorder = StreamRecorder()

    await roda(
        db,
        [
            FakeAdapter(
                [
                    ProviderEvent(
                        kind="tool_call",
                        tool_call=CanonicalToolCall(
                            id="c1", name="web_search", arguments='{"query":"Kairos"}'
                        ),
                    ),
                    ProviderEvent(kind="finish", finish_reason="tool_calls"),
                ]
            )
        ],
        streams=recorder,
    )

    # `tool_call` não é delta de stream: o observador quer a progressão do texto,
    # não o argumento de uma ferramenta no meio. O par start/end veio.
    assert [hook for hook, _ in recorder.vistos] == ["on_stream_start", "on_stream_end"]


@pytest.mark.anyio
async def test_o_runtime_enfileira_texto_e_reasoning_com_o_turno_real(db):
    recorder = StreamRecorder()
    eventos = [
        ProviderEvent(kind="reasoning", reasoning="pensando"),
        ProviderEvent(kind="text_delta", text="Resposta."),
        ProviderEvent(kind="finish", finish_reason="stop"),
    ]

    await roda(db, [FakeAdapter(eventos)], streams=recorder)

    deltas = [p for hook, p in recorder.vistos if hook == "on_stream_delta"]
    assert [(d["kind"], d["delta"]) for d in deltas] == [
        ("reasoning", "pensando"),
        ("text", "Resposta."),
    ]
    assert deltas[0]["conversation_id"] == "stream"
    assert deltas[0]["model"] == "test/model"
    assert deltas[0]["provider"] == "openrouter"
    assert deltas[0]["source"] == "web"


@pytest.mark.anyio
async def test_a_rodada_vira_tentativa_e_o_fim_do_stream_traz_o_texto_final(db):
    recorder = StreamRecorder()

    await roda(db, [FakeAdapter(texto_e_fim())], streams=recorder)

    inicio = next(p for hook, p in recorder.vistos if hook == "on_stream_start")
    fim = next(p for hook, p in recorder.vistos if hook == "on_stream_end")
    assert (inicio["attempt"], fim["attempt"]) == (1, 1)
    assert fim["final_text"] == "Resposta."
    assert fim["finished"] is True
    assert fim["error"] is None
    assert recorder.fechado == 1


@pytest.mark.anyio
async def test_a_retentativa_e_outra_tentativa_e_o_erro_vai_no_stream_end(db):
    recorder = StreamRecorder()

    await roda(
        db,
        [
            FakeAdapter([ProviderError(ProviderErrorKind.NETWORK, retryable=True)]),
            FakeAdapter(texto_e_fim("Depois da retentativa.")),
        ],
        streams=recorder,
    )

    inicios = [p for hook, p in recorder.vistos if hook == "on_stream_start"]
    fins = [p for hook, p in recorder.vistos if hook == "on_stream_end"]
    assert [i["attempt"] for i in inicios] == [1, 2]
    falhou, terminou = fins
    assert (falhou["finished"], falhou["attempt"]) == (False, 1)
    assert "ProviderError" in falhou["error"]
    assert (terminou["finished"], terminou["attempt"]) == (True, 2)
    assert terminou["final_text"] == "Depois da retentativa."


# ---------------------------------------------------------------------------
# plugin de verdade, no disco
# ---------------------------------------------------------------------------

PLUGIN_OBSERVADOR = """
import json
import os
import time


def _anota(nome, payload):
    with open(os.environ["OBSERVADOR_LOG"], "a", encoding="utf-8") as arquivo:
        arquivo.write(json.dumps({"hook": nome, "payload": dict(payload)}) + "\\n")


def on_stream_start(**payload):
    _anota("on_stream_start", payload)


def on_stream_delta(**payload):
    _anota("on_stream_delta", payload)
    payload["delta"] = "REESCRITO"
    libera = os.environ.get("OBSERVADOR_LIBERA")
    if libera:
        while not os.path.exists(libera):
            time.sleep(0.01)
        _anota("delta_soltou", {"delta": payload["delta"]})


def on_stream_end(**payload):
    _anota("on_stream_end", payload)


def pre_tool_call(**payload):
    _anota("pre_tool_call", payload)


def on_interim_message(**payload):
    _anota("on_interim_message", payload)
"""

CABECALHO_OBSERVADOR = 'name: observador\nversion: "1.0.0"\nkind: standalone\n'
# `requires_env` com o log: o plugin só é ativo quando existe para onde
# escrever, que é o mesmo caminho de um plugin de telemetria de verdade.
REQUER_LOG = "requires_env: [OBSERVADOR_LOG]\n"


def instala_observador(home: Path, *, hooks: str | None = None) -> Path:
    provides = hooks or "provides_hooks: [on_stream_start, on_stream_delta, on_stream_end]\n"
    pasta = home / "plugins" / "observador"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "plugin.yaml").write_text(
        f"{CABECALHO_OBSERVADOR}{provides}{REQUER_LOG}", encoding="utf-8"
    )
    (pasta / "plugin.py").write_text(PLUGIN_OBSERVADOR, encoding="utf-8")
    return pasta


def despachante_do_observador(home: Path) -> StreamHookDispatcher:
    loaded = load_plugins(home)
    assert loaded.registry.registered("on_stream_delta") == ["observador"]
    return StreamHookDispatcher(loaded.registry)


async def espera_algum(log: Path, hook: str, *, timeout: float = 5.0) -> bool:
    """Espera uma anotação do worker de stream, que roda em thread própria."""
    fim = time.monotonic() + timeout
    while time.monotonic() < fim:
        if hook in {a["hook"] for a in anotacoes(log)}:
            return True
        await asyncio.sleep(0.01)
    return False


def anotacoes(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(linha) for linha in log.read_text(encoding="utf-8").splitlines() if linha]


@pytest.mark.anyio
async def test_plugin_que_tenta_alterar_o_delta_nao_altera_o_texto_do_usuario(
    tmp_path, db, monkeypatch
):
    """Critério de aceitação do RF-08, com plugin de verdade em disco."""
    log = tmp_path / "observacao.jsonl"
    monkeypatch.setenv("OBSERVADOR_LOG", str(log))
    instala_observador(tmp_path)

    eventos = await roda(db, [FakeAdapter(texto_e_fim())], despachante_do_observador(tmp_path))

    assert "".join(e.text for e in eventos if e.kind == "delta") == "Resposta."
    # Sem ordem entre hooks (cada um tem seu worker), então o que se afirma é o
    # conteúdo de cada um: o plugin viu o texto original, mesmo tendo reescrito
    # o payload, e o `on_stream_end` chegou com o texto inteiro.
    visto = anotacoes(log)
    assert {a["hook"] for a in visto} == {
        "on_stream_start",
        "on_stream_delta",
        "on_stream_end",
    }
    delta = next(a for a in visto if a["hook"] == "on_stream_delta")
    assert delta["payload"]["delta"] == "Resposta."
    fim = next(a for a in visto if a["hook"] == "on_stream_end")
    assert fim["payload"]["final_text"] == "Resposta."


@pytest.mark.anyio
async def test_o_turno_termina_mesmo_com_o_observador_bloqueado(tmp_path, db, monkeypatch):
    """O callback segura uma thread; o `turn_end` do usuário não espera por ela."""
    log = tmp_path / "observacao.jsonl"
    libera = tmp_path / "libera"
    monkeypatch.setenv("OBSERVADOR_LOG", str(log))
    monkeypatch.setenv("OBSERVADOR_LIBERA", str(libera))
    instala_observador(tmp_path)
    service = make_service(
        db,
        [FakeAdapter(texto_e_fim())],
        streams=despachante_do_observador(tmp_path),
    )

    vistos: list[str] = []
    fim = asyncio.Event()

    async def consome():
        async for evento in service.stream(envelope_de_texto()):
            vistos.append(evento.kind)
            if evento.kind == "turn_end":
                fim.set()

    task = asyncio.ensure_future(consome())
    try:
        await asyncio.wait_for(fim.wait(), timeout=10)
        # `turn_end` já foi entregue e o observador continua preso no seu delta:
        # se o token esperasse o callback, o usuário não teria recebido isto.
        assert vistos[-1] == "turn_end"
        assert "delta_soltou" not in {a["hook"] for a in anotacoes(log)}
    finally:
        libera.write_text("pode", encoding="utf-8")
        await asyncio.wait_for(task, timeout=10)

    assert await espera_algum(log, "delta_soltou")
    assert "on_stream_end" in {a["hook"] for a in anotacoes(log)}


# ---------------------------------------------------------------------------
# a CLI continua dizendo a verdade
# ---------------------------------------------------------------------------


def test_hooks_list_distingue_emite_de_enfileira(tmp_path, monkeypatch, capsys):
    import contextlib
    import io
    import os

    from kairos_cli.main import main

    monkeypatch.setenv("OBSERVADOR_LOG", str(tmp_path / "observacao.jsonl"))
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    instala_observador(
        tmp_path,
        hooks="provides_hooks: [pre_tool_call, on_stream_delta, on_interim_message]\n",
    )

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        codigo = main(["hooks", "list"])
    saida = out.getvalue()

    assert codigo == 0, err.getvalue()
    assert os.environ["KAIROS_HOME"] == str(tmp_path)
    assert "hooks que o runtime emite (await)" in saida
    assert "hooks que o runtime enfileira (stream)" in saida
    assert "pre_tool_call  [emite]" in saida
    assert "on_stream_delta  [enfileira]" in saida
    # `on_interim_message` não tem superfície neste build: continua declarado e
    # reportado como sem emissor, em vez de virar lista de emissões.
    assert "on_interim_message  [SEM EMISSOR]" in saida
