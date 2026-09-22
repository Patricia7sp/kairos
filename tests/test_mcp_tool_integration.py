"""Passo 4 — ferramentas MCP de terceiros: toolset `mcp`, runtime e aprovação.

Comportamento real, não snapshot: os testes spawnam um servidor MCP `stdio`
falso de verdade (`tests/fake_mcp_server.py`) pelo pipe e conversam no
protocolo JSON-RPC por linha — sem mock de transporte. O cache é a única via
que não acorda processo; o sync é um caminho **explícito** que os testes
chamam para exercitar o spawn honesto.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

from kairos_integration.chat_tools import chat_tool_definitions, needs_tool_approval
from kairos_mcp.client import SchemaCache
from kairos_tools.mcp_tools import register_mcp_tools, server_configs, sync_mcp_servers
from kairos_tools.registry import ToolRegistry

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")

MANIFEST = [
    {
        "name": "sum",
        "description": "Soma dois números.",
        "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}}},
    },
    {
        "name": "ola",
        "description": "Cumprimenta.",
        "inputSchema": {"type": "object", "properties": {"nome": {"type": "string"}}},
    },
]


def _write_mcp_config(home, server: str = "fake", **spec):
    base = {"transport": "stdio", "command": sys.executable, "args": [str(FAKE_SERVER)]}
    base.update(spec)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"mcp_servers": {server: base}}, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    return tmp_path


def _register(home) -> ToolRegistry:
    reg = ToolRegistry()
    register_mcp_tools(reg)
    return reg


def _names(reg: ToolRegistry) -> set[str]:
    return {name for name in reg.get_all_tool_names() if name.startswith("mcp__")}


# ---------------------------------------------------------------------------
# Gate: config é a origem da verdade
# ---------------------------------------------------------------------------


def test_sem_mcp_servers_nao_registra_nada(home):
    reg = _register(home)
    assert reg.is_toolset_available("mcp") is False
    assert _names(reg) == set()


def test_config_invalida_nao_expoe_ferramenta(home):
    _write_mcp_config(home, command="/bin/sh", args=("-c", "ls"))
    reg = _register(home)
    assert reg.is_toolset_available("mcp") is False
    assert _names(reg) == set()
    assert server_configs(home) == []


def test_transporte_nao_stdio_e_recusado_barulhento_nao_silencioso(home):
    """Aceitar um servidor http e nunca executar seria o bug "reporta sucesso
    sem efeito" — o slice entrega cliente stdio e recusa em voz alta."""
    _write_mcp_config(home, transport="http", url="https://exemplo.com")
    assert server_configs(home) == []
    reg = _register(home)
    assert _names(reg) == set()


# ---------------------------------------------------------------------------
# Cache: registrar sem acordar processo
# ---------------------------------------------------------------------------


def test_registra_do_cache_sem_spawn(home, monkeypatch):
    _write_mcp_config(home)
    SchemaCache(home / "mcp").store("fake", MANIFEST)

    def _jamais_spawn(*_args, **_kw):
        raise AssertionError("registro do cache NÃO pode spawnar o servidor")

    monkeypatch.setattr("kairos_tools.mcp_tools.fetch_tool_manifests", _jamais_spawn)
    reg = _register(home)
    assert reg.is_toolset_available("mcp") is True
    assert "mcp__fake__sum" in _names(reg)
    assert "mcp__fake__ola" in _names(reg)
    definitions = {d["function"]["name"]: d["function"] for d in reg.get_definitions()}
    fn = definitions["mcp__fake__sum"]
    assert fn["parameters"]["type"] == "object"
    assert "a" in fn["parameters"]["properties"]


def test_offline_forca_esperar_sem_spawn_e_fecha_sem_ferramentas(home, monkeypatch, caplog):
    _write_mcp_config(home)
    monkeypatch.setenv("KAIROS_MCP_OFFLINE", "1")

    def _jamais_spawn(*_args, **_kw):
        raise AssertionError("KAIROS_MCP_OFFLINE impede qualquer spawn no registro")

    monkeypatch.setattr("kairos_tools.mcp_tools.fetch_tool_manifests", _jamais_spawn)
    reg = _register(home)
    assert reg.is_toolset_available("mcp") is True  # config existe e valida
    assert _names(reg) == set()  # mas zero ferramentas até sincronizar
    assert "KAIROS_MCP_OFFLINE" in caplog.text


# ---------------------------------------------------------------------------
# Sync: spawn real pelo pipe
# ---------------------------------------------------------------------------


def test_sync_spawna_servidor_real_e_popula_o_cache(home):
    _write_mcp_config(home)
    relatorio = sync_mcp_servers(home)
    assert relatorio["sincronizados"] == ["fake"]
    assert relatorio["erros"] == []
    tools = json.loads((home / "mcp" / "fake.json").read_text(encoding="utf-8"))
    nomes = {entry["name"] for entry in tools}
    assert {
        "sum",
        "echo_notifica",
        "ping_probe",
        "com_bloco",
        "ruidoso",
        "explode",
        "morre",
    } <= nomes


def test_catalogo_paginado_e_coletado_integralmente(home):
    _write_mcp_config(home, server="pag", env={"KAIROS_FAKE_MCP_PAGES": "2"})
    relatorio = sync_mcp_servers(home)
    assert relatorio["erros"] == []
    tools = json.loads((home / "mcp" / "pag.json").read_text(encoding="utf-8"))
    assert {"sum", "morre", "ping_probe"} <= {entry["name"] for entry in tools}


def test_servidor_que_recusa_o_initialize_falha_nomeado_no_sync(home):
    _write_mcp_config(home, env={"KAIROS_FAKE_MCP_REFUSE": "1"})
    relatorio = sync_mcp_servers(home)
    assert relatorio["sincronizados"] == []
    assert len(relatorio["erros"]) == 1
    assert "recusa deliberada" in relatorio["erros"][0]


# ---------------------------------------------------------------------------
# Dispatch: a ferramenta roda no servidor de verdade
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_reg(home, monkeypatch):
    _write_mcp_config(home)
    relatorio = sync_mcp_servers(home)
    assert relatorio["erros"] == []
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    return _register(home)


def test_call_executa_ferramenta_real_no_pipe(mcp_reg):
    assert mcp_reg.dispatch("mcp__fake__sum", {"a": 2, "b": 3}) == "5"


def test_notificacao_e_requisicao_do_servidor_nao_interrompem_a_resposta(mcp_reg):
    assert mcp_reg.dispatch("mcp__fake__echo_notifica", {"texto": "oi"}) == "oi"
    assert mcp_reg.dispatch("mcp__fake__ping_probe") == "ping ok"


def test_stderr_do_servidor_e_drenado_sem_travar_a_resposta(mcp_reg):
    assert mcp_reg.dispatch("mcp__fake__ruidoso") == "ruído ok"


def test_bloco_nao_textual_vira_descriptor_honesto(mcp_reg):
    saida = mcp_reg.dispatch("mcp__fake__com_bloco")
    assert saida == "verde\n[bloco MCP image — file:///tmp/x.png]"


def test_isError_do_servidor_e_falha_nomeada(mcp_reg):
    resultado = mcp_reg.dispatch("mcp__fake__explode")
    assert "retornou erro no servidor" in resultado["error"]


def test_servidor_que_morre_no_call_fecha_sem_sucesso(mcp_reg):
    resultado = mcp_reg.dispatch("mcp__fake__morre")
    assert "fechou a conexão" in resultado["error"]


# ---------------------------------------------------------------------------
# Superfície do Chat: expõe o prefixo e aprovação é SEMPRE obrigatória
# ---------------------------------------------------------------------------


def test_chat_expoe_mcp_e_aprova_toda_chamada(mcp_reg):
    definitions = mcp_reg.get_definitions()
    out = tuple(d for d in chat_tool_definitions(definitions=definitions) if "mcp__" in str(d))
    assert any(d["function"]["name"] == "mcp__fake__sum" for d in out)
    assert needs_tool_approval("mcp__fake__sum") is True
    assert needs_tool_approval("mcp__fake__ola") is True


def test_chat_nao_expoe_mcp_sem_config(home):
    reg = _register(home)
    out = chat_tool_definitions(definitions=reg.get_definitions())
    assert all(not d["function"]["name"].startswith("mcp__") for d in out)
    assert needs_tool_approval("web_search") is False
