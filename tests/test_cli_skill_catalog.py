"""CLI real com catálogo persistido e adapter local fictício."""

import asyncio
import hashlib
import io
import json

import pytest
from test_chat_search_loop import RoundGateway, answer_round
from test_chat_tool_approval import mutator_round
from test_interaction_skills import skill_payload
from test_skill_runtime import skill_text, write_skill

from kairos_cli import chat
from kairos_cli.main import main
from kairos_integration import composition
from kairos_providers import (
    CanonicalToolCall,
    CatalogModel,
    CatalogOrigin,
    ModelCapabilities,
    ModelCatalog,
    ProviderModelRef,
)
from kairos_state import connect


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("provider: ollama\nmodel: fixture-local\n")

    def build(*, read=True, tools=True):
        rounds = (
            [
                mutator_round(CanonicalToolCall("view", "skill_view", '{"name":"revisar-docs"}')),
                answer_round(),
            ]
            if read
            else [answer_round()]
        )

        class LocalGateway(RoundGateway):
            async def aclose(self):
                self.closed = True

            def prepare(self, ref):
                prepared = super().prepare(ref)
                prepared.credential_id = None
                return prepared

        gateway = LocalGateway(rounds)
        gateway.closed = False
        gateway.catalog = ModelCatalog()
        gateway.catalog.merge(
            [
                CatalogModel(
                    ref=ProviderModelRef("ollama", "fixture-local"),
                    display_name="Local fictício",
                    capabilities=ModelCapabilities(chat=True, tools=tools),
                )
            ],
            origin=CatalogOrigin.CURATED,
        )
        monkeypatch.setattr(composition, "build_provider_gateway", lambda _: gateway)
        return gateway

    return build


def options(command="run", *, catalog=True, extra=()):
    return [
        command,
        "--session",
        "catalog-cli",
        "--no-experiences",
        *(["--skills-catalog"] if catalog else []),
        *extra,
        "pedido",
    ]


@pytest.mark.parametrize("command", ["run", "chat"])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_indice_e_leitura_entregues_ao_adapter(
    installed, tmp_path, monkeypatch, capsys, *, command, as_json
):
    text = skill_text(body="corpo privado fictício á🙂")
    write_skill(tmp_path, text=text)
    gateway = installed()
    stdin = io.StringIO("não consumir")
    monkeypatch.setattr("sys.stdin", stdin)
    assert main(options(command, extra=["--json"] if as_json else [])) == 0
    first, second = gateway.requests
    assert first.model == ProviderModelRef("ollama", "fixture-local")
    assert first.tools[0]["function"]["name"] == "skill_view"
    assert "Revise documentos." in first.messages[0].content[0].value
    assert "corpo privado fictício" not in first.messages[0].content[0].value
    result = next(m for m in second.messages if m.role == "tool")
    assert json.loads(result.content[0].value)["text"] == text
    assert stdin.tell() == 0
    assert gateway.closed
    output = capsys.readouterr()
    assert "Catálogo de skills:" in output.err
    assert "corpo privado fictício" not in output.err
    assert "Revise documentos." not in output.err
    assert str(tmp_path) not in output.err
    if as_json:
        events = [json.loads(line) for line in output.out.splitlines()]
        assert events[-1]["type"] == "turn_end"
        assert all(e["type"] != "skill_catalog_ready" and "catalog_notice" not in e for e in events)
    with connect(tmp_path / "state.db") as db:
        assert (
            db.execute("SELECT content FROM messages WHERE role='user'").fetchone()[0] == "pedido"
        )
        assert db.execute("SELECT text FROM skill_catalog_assets").fetchone()[0] == text


def test_sem_flag_posterior_preserva_historico_sem_habilitar_leitura(
    installed, tmp_path, monkeypatch
):
    write_skill(tmp_path)
    installed()
    assert main(options()) == 0
    gateway = installed(read=False)
    monkeypatch.setattr(
        "kairos_integration.skill_catalog_turn.capture_skill_catalog",
        lambda *_: pytest.fail("capturou"),
    )
    assert main(options(catalog=False)) == 0
    request = gateway.requests[0]
    assert not request.tools
    assert all(
        "Catálogo de skills instalado" not in m.content[0].value
        for m in request.messages
        if m.role == "system"
    )
    assert any(
        m.role == "tool" and skill_text() in json.loads(m.content[0].value)["text"]
        for m in request.messages
    )


def test_catalogo_coexiste_com_skill_explicita_e_versoes_distintas(installed, tmp_path):
    old = skill_text(body="procedimento antigo")
    path = write_skill(tmp_path, text=old)
    installed()
    assert main(options()) == 0
    new = skill_text(body="procedimento novo").replace("Revise documentos.", "Descrição atual.")
    path.write_text(new)
    gateway = installed()
    assert main(options(extra=["--skill", "revisar-docs"])) == 0
    request = gateway.requests[0]
    assert "Revise documentos." in request.messages[0].content[0].value
    assert "Descrição atual." not in request.messages[0].content[0].value
    selected, _ = skill_payload(request.messages[-1])
    assert selected[0]["text"] == new
    assert selected[0]["sha256"] == hashlib.sha256(new.encode()).hexdigest()
    results = [
        json.loads(m.content[0].value) for m in gateway.requests[-1].messages if m.role == "tool"
    ]
    assert results[-1]["text"] == old
    assert results[-1]["sha256"] == hashlib.sha256(old.encode()).hexdigest()


@pytest.mark.parametrize("runtime", [False, True])
def test_catalogo_interativo_ou_runtime_recusa_antes_de_compor(tmp_path, monkeypatch, runtime):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(chat, "_is_runtime_session", lambda *_: runtime)
    monkeypatch.setattr(chat, "build_interaction_service", lambda _: pytest.fail("compôs"))
    args = ["chat", "--session", "s", "--skills-catalog"]
    if runtime:
        args.append("pedido")
    assert main(args) == 2


@pytest.mark.parametrize("tools", [False, None])
def test_modelo_sem_tools_recusa_sem_request_ou_user(installed, tmp_path, tools):
    write_skill(tmp_path)
    gateway = installed(tools=tools)
    assert main(options()) == 1
    assert gateway.requests == [] and gateway.preparations == 0
    with connect(tmp_path / "state.db") as db:
        assert db.execute("SELECT COUNT(*) FROM messages WHERE role='user'").fetchone()[0] == 0


def test_catalogo_invalido_recusa_sem_dados_privados(installed, tmp_path, capsys):
    write_skill(tmp_path, text="conteúdo privado inválido")
    gateway = installed()
    assert main(options()) == 1
    assert gateway.requests == [] and gateway.preparations == 0
    output = capsys.readouterr()
    assert "conteúdo privado inválido" not in output.err
    assert str(tmp_path) not in output.err


def test_flag_exige_bool_antes_de_io(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "_is_runtime_session", lambda *_: pytest.fail("leu DB"))
    with pytest.raises(chat.ChatUsageError):
        asyncio.run(
            chat.run_chat(
                home=tmp_path,
                session_id="s",
                prompt="pedido",
                provider=None,
                model=None,
                as_json=False,
                skills_catalog=1,
            )
        )
