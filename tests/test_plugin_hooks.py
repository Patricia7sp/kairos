"""Hooks de plugin: descoberta, registro, emissor e o que o turno dispara.

O que estes testes afirmam é **relação entre duas coisas**, nunca o valor
atual de um catálogo: que o que o manifesto declara é o que entra no registro, que
o que o registro tem é o que o emissor dispara, e que o que o emissor dispara é
o que o turno realmente executou. Nenhum teste lê o código-fonte.
"""

import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from kairos_integration import InteractionEnvelope, InteractionToolResult
from kairos_integration.interaction_service import InteractionService
from kairos_plugins import (
    EMITTED_HOOKS,
    HookEmitter,
    HookRegistry,
    PluginState,
    UnknownHook,
    disabled_from_config,
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
    ProviderEvent,
    ProviderModelRef,
    TokenUsage,
)
from kairos_providers.gateway import ProviderBillingMetadata
from kairos_providers.selection import ModelSelectionResolver
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository, UsageRepository

# ---------------------------------------------------------------------------
# descoberta
# ---------------------------------------------------------------------------


def write_plugin(home, nome, *, manifest, modulo=None):
    """Plugin no layout da spec: `plugin.yaml` + `plugin.py` opcional."""
    return write_plugin_dir(home / "plugins", nome, manifest=manifest, modulo=modulo)


def write_plugin_dir(raiz, nome, *, manifest, modulo=None):
    pasta = raiz / nome
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "plugin.yaml").write_text(manifest, encoding="utf-8")
    if modulo is not None:
        (pasta / "plugin.py").write_text(modulo, encoding="utf-8")
    return pasta


def manifest_de(nome, *hooks, extra=""):
    return (
        f'name: {nome}\nversion: "1.0.0"\nkind: standalone\n'
        f"provides_hooks: [{', '.join(hooks)}]\n{extra}"
    )


def test_manifesto_declara_e_modulo_implementa_registra_os_dois(tmp_path):
    write_plugin(
        tmp_path,
        "contador",
        manifest=manifest_de("contador", "pre_tool_call", "post_tool_call"),
        modulo=(
            "VISTOS = []\n\n\n"
            "def pre_tool_call(*, tool, **_):\n    VISTOS.append(('pre', tool))\n\n\n"
            "def post_tool_call(*, tool, **_):\n    VISTOS.append(('post', tool))\n\n\n"
            "def nao_declarado(**_):\n    raise AssertionError('não deveria ser chamado')\n"
        ),
    )
    carregados = load_plugins(tmp_path)

    plugin = carregados.plugins[0]
    assert plugin.state is PluginState.ACTIVE
    assert set(plugin.registered_hooks) == {"pre_tool_call", "post_tool_call"}
    assert carregados.registry.hooks_with_callbacks() == {
        "post_tool_call": ["contador"],
        "pre_tool_call": ["contador"],
    }


def test_callback_implementado_mas_nao_declarado_nao_entra(tmp_path):
    """`provides_hooks` é a única fonte — o inverso é o hooks.json apagado."""
    write_plugin(
        tmp_path,
        "extra",
        manifest=manifest_de("extra", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n\n\ndef post_tool_call(**_):\n    return 2\n",
    )
    carregados = load_plugins(tmp_path)

    assert carregados.plugins[0].registered_hooks == ("pre_tool_call",)
    assert "post_tool_call" not in carregados.registry.hooks_with_callbacks()


def test_ativo_sem_modulo_fica_visivel_sem_callback(tmp_path):
    write_plugin(tmp_path, "sem-corpo", manifest=manifest_de("sem-corpo", "pre_tool_call"))

    plugin = load_plugins(tmp_path).plugins[0]

    assert plugin.state is PluginState.ACTIVE
    assert plugin.registered_hooks == ()
    assert plugin.error and "plugin.py" in plugin.error


def test_manifesto_invalido_nao_derruba_o_vecinho(tmp_path):
    write_plugin(tmp_path, "quebrado", manifest="name: quebrado\nversion: nao-e-versao\n")
    write_plugin(
        tmp_path,
        "saudavel",
        manifest=manifest_de("saudavel", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    por_nome = {p.name: p for p in load_plugins(tmp_path).plugins}

    assert por_nome["quebrado"].error
    assert por_nome["saudavel"].registered_hooks == ("pre_tool_call",)


def test_requires_env_ausente_desabilita_e_diz_o_que_falta(tmp_path):
    write_plugin(
        tmp_path,
        "precisa-chave",
        manifest=manifest_de(
            "precisa-chave", "pre_tool_call", extra="requires_env: [MINHA_CHAVE]\n"
        ),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    plugin = load_plugins(tmp_path, env={}).plugins[0]

    assert plugin.state is PluginState.DISABLED_MISSING_ENV
    assert plugin.missing_env == ("MINHA_CHAVE",)
    assert plugin.registered_hooks == ()


def test_env_preenchido_ativa_o_plugin(tmp_path):
    write_plugin(
        tmp_path,
        "precisa-chave",
        manifest=manifest_de(
            "precisa-chave", "pre_tool_call", extra="requires_env: [MINHA_CHAVE]\n"
        ),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    plugin = load_plugins(tmp_path, env={"MINHA_CHAVE": "x"}).plugins[0]

    assert plugin.state is PluginState.ACTIVE
    assert plugin.registered_hooks == ("pre_tool_call",)


def test_plugins_disabled_tira_o_plugin_do_registro(tmp_path):
    write_plugin(
        tmp_path,
        "opcional",
        manifest=manifest_de("opcional", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    raise AssertionError('plugin desligado rodou')\n",
    )

    carregados = load_plugins(tmp_path, disabled=("opcional",))

    assert carregados.plugins[0].state is PluginState.DISABLED
    assert carregados.registry.hooks_with_callbacks() == {}


def test_modulo_que_levanta_nao_derruba_o_host(tmp_path):
    write_plugin(
        tmp_path,
        "explosivo",
        manifest=manifest_de("explosivo", "pre_tool_call"),
        modulo="raise RuntimeError('oi')\n",
    )
    write_plugin(
        tmp_path,
        "saudavel",
        manifest=manifest_de("saudavel", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    carregados = load_plugins(tmp_path)

    por_nome = {p.name: p for p in carregados.plugins}
    assert "RuntimeError" in (por_nome["explosivo"].error or "")
    assert carregados.registry.hooks_with_callbacks() == {"pre_tool_call": ["saudavel"]}


def test_bundled_tem_precedencia_sobre_o_usuario(tmp_path):
    bundled = tmp_path / "bundled"
    write_plugin_dir(
        bundled,
        "mesmo-nome",
        manifest=manifest_de("mesmo-nome", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 'bundled'\n",
    )
    write_plugin(
        tmp_path,
        "mesmo-nome",
        manifest=manifest_de("mesmo-nome", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 'usuario'\n",
    )

    carregados = load_plugins(tmp_path, bundled=bundled)

    assert [p.path for p in carregados.plugins] == [bundled / "mesmo-nome"]


def test_home_sem_pasta_de_plugins_le_o_inventario_vazio(tmp_path):
    carregados = load_plugins(tmp_path)

    assert carregados.empty
    assert carregados.registry.hooks_with_callbacks() == {}


# ---------------------------------------------------------------------------
# config: parse fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "config",
    [
        {"plugins": [1, 2]},
        {"plugins": {"disabled": "contador"}},
        {"plugins": {"disabled": {"a": 1}}},
        {"plugins": {"disabled": [1]}},
    ],
)
def test_disabled_malformado_levanta_em_vez_de_desligar_nada(config):
    with pytest.raises(ValueError):
        disabled_from_config(config)


@pytest.mark.parametrize(
    "config,esperado",
    [({}, ()), ({"plugins": {}}, ()), ({"plugins": {"disabled": ["a", "b"]}}, ("a", "b"))],
)
def test_disabled_bem_formado_vira_lista_de_nomes(config, esperado):
    assert disabled_from_config(config) == esperado


# ---------------------------------------------------------------------------
# emissor
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_emissor_dispara_o_que_o_registro_tem_e_nao_o_que_nao_tem():
    registro = HookRegistry()
    registro.register("pre_tool_call", lambda **_: None, plugin="p")
    emitter = HookEmitter(registro)

    await emitter.emit("pre_tool_call", tool="bash")
    await emitter.emit("post_tool_call", tool="bash")  # ninguém escuta este

    assert registro.hooks_with_callbacks() == {"pre_tool_call": ["p"]}


@pytest.mark.anyio
async def test_emissor_recusa_hook_sem_efeito_declarado():
    """Nome de hook que o runtime não emite é erro, não no-op silencioso."""
    emitter = HookEmitter(HookRegistry())

    with pytest.raises(UnknownHook) as erro:
        await emitter.emit("transform_tool_result", tool="bash", result={})

    assert "transform_tool_result" in str(erro.value)


def test_emissor_sem_nenhum_plugin_nao_faz_nada():
    """Custo zero: `build_plugin_hooks` devolve `None` e o caminho nem chega aqui."""
    emitter = HookEmitter(HookRegistry())

    assert emitter.enabled is False


@pytest.mark.anyio
async def test_callback_que_levanta_nao_impede_o_proximo(tmp_path):
    write_plugin(
        tmp_path,
        "ruim",
        manifest=manifest_de("ruim", "pre_tool_call", "post_tool_call"),
        modulo=(
            "VISTOS = []\n\n\n"
            "def pre_tool_call(**_):\n    raise RuntimeError('plugin ruim')\n\n\n"
            "def post_tool_call(*, tool, **_):\n    VISTOS.append(tool)\n"
        ),
    )
    carregados = load_plugins(tmp_path)
    emissor = HookEmitter(carregados.registry)
    sobreviveu: list[str] = []
    carregados.registry.register(
        "post_tool_call", lambda *, tool, **_: sobreviveu.append(tool), plugin="teste"
    )

    await emissor.emit("pre_tool_call", tool="bash")
    await emissor.emit("post_tool_call", tool="bash")

    assert sobreviveu == ["bash"]


# ---------------------------------------------------------------------------
# o turno dispara o que o emissor tem
# ---------------------------------------------------------------------------


class RoundGateway:
    def __init__(self, rounds):
        self.rounds = iter(rounds)

    def prepare(self, ref):
        return SimpleNamespace(
            credential_id="test-key",
            price=ModelPrice(
                prompt=Decimal("0.001"), completion=Decimal("0.002"), request=Decimal("0.01")
            ),
            cost_source="catalog",
            create_adapter=lambda: self,
            billing=ProviderBillingMetadata(ref.provider, "https://example.test", "api_key"),
        )

    async def stream(self, request):
        for event in next(self.rounds):
            yield event


class Recorder:
    """Emissor de mentira que grava a ordem real dos disparos."""

    def __init__(self):
        self.vistos: list[tuple[str, dict]] = []
        self.enabled = True

    async def emit(self, hook, **payload):
        self.vistos.append((hook, payload))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    yield connection
    connection.close()


def make_service(db, gateway, hooks):
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
        gateway=gateway,
        resolver=ModelSelectionResolver(catalog),
        context_loader=SimpleNamespace(
            load=lambda _: ModelSelectionContext(
                global_default=ProviderModelRef("openrouter", "test/model")
            )
        ),
        sessions=SessionRepository(db),
        messages=MessageRepository(db),
        usage=UsageRepository(db),
        hooks=hooks,
    )


def turnos():
    return [
        [
            ProviderEvent(
                kind="tool_call",
                tool_call=CanonicalToolCall(
                    id="c1", name="web_search", arguments='{"query":"Kairos"}'
                ),
            ),
            ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=3, output_tokens=2)),
            ProviderEvent(kind="finish", finish_reason="tool_calls"),
        ],
        [
            ProviderEvent(kind="text_delta", text="Resposta."),
            ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=7, output_tokens=4)),
            ProviderEvent(kind="finish", finish_reason="stop"),
        ],
    ]


async def roda_um_turno(db, hooks, monkeypatch, **tool_flags):
    from unittest.mock import AsyncMock

    busca = AsyncMock(return_value=InteractionToolResult("c1", '{"results":["fonte"]}'))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", busca)
    service = make_service(db, RoundGateway(turnos()), hooks)
    envelope = InteractionEnvelope(
        conversation_id="hooks",
        source="web",
        content="Pesquise",
        parameters={"routing": {"data_collection": "deny"}},
        **tool_flags,
    )
    return [event async for event in service.stream(envelope)], busca


@pytest.mark.anyio
async def test_turno_dispara_a_sequencia_de_observacao(db, monkeypatch):
    recorder = Recorder()

    eventos, busca = await roda_um_turno(db, recorder, monkeypatch, web_search=True)

    # A ferramenta é executada quando a rodada que a pediu termina, então o par
    # pre/post fica entre as rodadas do provedor, não dentro delas.
    assert [hook for hook, _ in recorder.vistos] == [
        "on_session_start",
        "pre_llm_call",
        "post_llm_call",
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "post_llm_call",
        "on_session_end",
    ]
    pre = next(p for h, p in recorder.vistos if h == "pre_tool_call")
    assert pre["tool"] == "web_search"
    assert json.loads(pre["arguments"])["query"] == "Kairos"
    pos = next(p for h, p in recorder.vistos if h == "post_tool_call")
    assert pos["is_error"] is False
    assert busca.await_count == 1
    assert eventos[-1].kind == "turn_end"
    # Fechamento normal do async generator não é "interrompido": o `outcome`
    # que o observador lê é o desfecho do turno, não o da consumption.
    inicio = next(p for h, p in recorder.vistos if h == "on_session_start")
    fim = next(p for h, p in recorder.vistos if h == "on_session_end")
    assert fim["outcome"] == "turn_end"
    assert inicio["conversation_id"] == fim["conversation_id"] == "hooks"


@pytest.mark.anyio
async def test_turno_interrompido_avisa_interrompido(db, monkeypatch):
    """Desconexão no meio é desfecho diferente, e o hook continua sendo avisado."""
    import asyncio

    started = asyncio.Event()
    nunca = asyncio.Event()

    async def ferramenta_pendente(call, *, execute):
        started.set()
        await nunca.wait()
        return InteractionToolResult(call.id, "{}")

    monkeypatch.setattr(
        "kairos_integration.interaction_service.execute_chat_tool", ferramenta_pendente
    )
    recorder = Recorder()
    service = make_service(db, RoundGateway(turnos()), recorder)
    envelope = InteractionEnvelope(
        conversation_id="hooks-corta",
        source="web",
        content="Pesquise",
        parameters={"routing": {"data_collection": "deny"}},
        web_search=True,
    )
    task = asyncio.ensure_future(_consome(service.stream(envelope)))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    fim = next(p for h, p in recorder.vistos if h == "on_session_end")
    assert fim["outcome"] == "interrompido"


@pytest.mark.anyio
async def test_ferramenta_que_falha_ainda_avisa_o_post(db, monkeypatch):
    """O par pre/post é do **evento**, não do sucesso: `is_error` é o sinal."""
    recorder = Recorder()
    from unittest.mock import AsyncMock

    falha = AsyncMock(return_value=InteractionToolResult("c1", "boom", is_error=True))
    monkeypatch.setattr("kairos_integration.interaction_service.execute_chat_tool", falha)

    service = make_service(db, RoundGateway(turnos()), recorder)
    envelope = InteractionEnvelope(
        conversation_id="hooks-erro",
        source="web",
        content="Pesquise",
        parameters={"routing": {"data_collection": "deny"}},
        web_search=True,
    )
    eventos = [event async for event in service.stream(envelope)]

    pos = next(p for h, p in recorder.vistos if h == "post_tool_call")
    assert pos["is_error"] is True
    assert eventos[-1].kind == "turn_end"


@pytest.mark.anyio
async def test_hooks_disparam_a_partir_do_registro_real(tmp_path, db, monkeypatch):
    """Ponte completa: `<home>/plugins` -> registro -> emissor -> turno."""
    write_plugin(
        tmp_path / "home",
        "espiador",
        manifest=manifest_de("espiador", "pre_tool_call"),
        modulo=(
            "import json, pathlib\n\n"
            "ARQUIVO = pathlib.Path(__file__).with_name('vistos.json')\n\n\n"
            "def pre_tool_call(*, tool, **_):\n"
            "    dados = json.loads(ARQUIVO.read_text()) if ARQUIVO.exists() else []\n"
            "    dados.append(tool)\n"
            "    ARQUIVO.write_text(json.dumps(dados))\n"
        ),
    )
    carregados = load_plugins(tmp_path / "home")
    emitter = HookEmitter(carregados.registry)

    await roda_um_turno(db, emitter, monkeypatch, web_search=True)

    vistos = json.loads((tmp_path / "home" / "plugins" / "espiador" / "vistos.json").read_text())
    assert vistos == ["web_search"]


def test_emitted_hooks_sao_hooks_que_o_registro_aceita():
    from kairos_plugins import VALID_HOOKS

    assert set(EMITTED_HOOKS) <= VALID_HOOKS


# ---------------------------------------------------------------------------
# composição: a ponte que decide se existe emitter
# ---------------------------------------------------------------------------


def test_sem_plugins_nao_existe_emitter(tmp_path):
    from kairos_integration.composition import build_plugin_hooks

    assert build_plugin_hooks(tmp_path, {}) == (None, None)


def test_plugin_ativo_produz_emitter(tmp_path):
    from kairos_integration.composition import build_plugin_hooks

    write_plugin(
        tmp_path,
        "presente",
        manifest=manifest_de("presente", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    emitter, streams = build_plugin_hooks(tmp_path, {})

    assert emitter is not None
    assert emitter.enabled is True
    # D-PLUG.7: os dois saem do mesmo registro, mesmo com plugin que só escuta
    # `pre_tool_call` — o despachante existe para ser `None` só quando não há
    # plugin, não quando não há observador de stream.
    assert streams is not None
    assert streams.enabled is True
    assert streams.listening("on_stream_delta") is False


def test_config_malformado_derruba_a_composicao(tmp_path):
    """`plugins.disabled: sim` é erro de configuração, não 'ninguém desligado'."""
    from kairos_integration.composition import build_plugin_hooks

    with pytest.raises(ValueError):
        build_plugin_hooks(tmp_path, {"plugins": {"disabled": "presente"}})


def test_plugin_desligado_nao_produz_emitter(tmp_path):
    from kairos_integration.composition import build_plugin_hooks

    write_plugin(
        tmp_path,
        "presente",
        manifest=manifest_de("presente", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    emitter, streams = build_plugin_hooks(tmp_path, {"plugins": {"disabled": ["presente"]}})

    assert (emitter is None or emitter.enabled is False) and (
        streams is None or streams.enabled is False
    )


# ---------------------------------------------------------------------------
# CLI: o inventário que a CLI mostra é o que existe
# ---------------------------------------------------------------------------


def roda_cli(argv, home):
    """Roda a CLI de verdade com `KAIROS_HOME` apontado para um home temporário."""
    import contextlib
    import io
    import os

    from kairos_cli.main import main

    anterior = os.environ.get("KAIROS_HOME")
    os.environ["KAIROS_HOME"] = str(home)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            codigo = main(argv)
    finally:
        if anterior is None:
            os.environ.pop("KAIROS_HOME", None)
        else:
            os.environ["KAIROS_HOME"] = anterior
    return codigo, out.getvalue() + err.getvalue()


def test_plugins_list_mostra_o_que_o_registro_ficou(tmp_path):
    write_plugin(
        tmp_path,
        "visivel",
        manifest=manifest_de("visivel", "pre_tool_call", "post_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n\n\ndef post_tool_call(**_):\n    return 2\n",
    )

    codigo, saida = roda_cli(["plugins", "list"], tmp_path)

    assert codigo == 0
    assert "visivel" in saida
    assert "pre_tool_call" in saida and "post_tool_call" in saida


def test_plugins_list_diz_nenhum_quando_nao_ha_plugins(tmp_path):
    codigo, saida = roda_cli(["plugins", "list"], tmp_path)

    assert codigo == 0
    assert "nenhum" in saida


def test_hooks_list_separa_emissor_de_nome(tmp_path):
    write_plugin(
        tmp_path,
        "espiador",
        manifest=manifest_de("espiador", "pre_tool_call"),
        modulo="def pre_tool_call(**_):\n    return 1\n",
    )

    codigo, saida = roda_cli(["hooks", "list"], tmp_path)

    assert codigo == 0
    assert "pre_tool_call" in saida
    assert "espiador" in saida
    assert "hooks sem emissor" in saida


def test_hooks_use_ainda_recusa_com_69(tmp_path):
    codigo, saida = roda_cli(["hooks", "use", "--hook", "pre_tool_call"], tmp_path)

    assert codigo == 69
    assert "não implementado" in saida
    assert "hooks.json" in saida


@pytest.mark.anyio
async def test_hook_que_levanta_nao_interrompe_o_turno(db, monkeypatch, caplog):
    """D-PLUG.6: a falha do hook é log; o `turn_end` do runtime é o desfecho."""
    import asyncio

    class Hostil:
        enabled = True

        async def emit(self, hook, **_):
            if hook == "on_session_end":
                raise asyncio.CancelledError("o plugin quer cancelar o turno")

    with caplog.at_level("WARNING", logger="kairos_integration.interaction_service"):
        eventos, _ = await roda_um_turno(db, Hostil(), monkeypatch, web_search=True)

    assert eventos[-1].kind == "turn_end"
    assert any("on_session_end" in registro.message for registro in caplog.records), caplog.text


@pytest.mark.anyio
async def test_cancelamento_de_quem_aguarda_continua_saindo(db, monkeypatch):
    """O hook calado não engole o cancelamento externo — é o waiter que cancela."""
    import asyncio

    started = asyncio.Event()
    nunca = asyncio.Event()

    async def ferramenta_pendente(call, *, execute):
        started.set()
        await nunca.wait()
        return InteractionToolResult(call.id, "{}")

    monkeypatch.setattr(
        "kairos_integration.interaction_service.execute_chat_tool", ferramenta_pendente
    )

    class Calado:
        enabled = True

        async def emit(self, hook, **_):
            return None

    service = make_service(db, RoundGateway(turnos()), Calado())
    envelope = InteractionEnvelope(
        conversation_id="hooks-cancel",
        source="web",
        content="Pesquise",
        parameters={"routing": {"data_collection": "deny"}},
        web_search=True,
    )
    task = asyncio.ensure_future(_consome(service.stream(envelope)))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _consome(stream):
    return [event async for event in stream]
