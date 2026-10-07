"""Fronteira de workspace pelo despacho real da CLI."""

import asyncio
import contextvars
import json
import os
import sys
from pathlib import Path
from textwrap import dedent

import pytest
from test_cli_tools import install as cli_install
from test_cli_tools import terminal_input

from kairos_cli.main import main
from kairos_providers import CanonicalToolCall
from kairos_tools import workspace as fs
from kairos_tools.builtin import register_builtin_tools
from kairos_tools.registry import ToolRegistry

install = cli_install


@pytest.mark.parametrize("command", ["run", "chat"])
@pytest.mark.parametrize("escape", ["absolute", "relative", "symlink"])
def test_cli_recusa_leitura_fora_do_workspace(install, tmp_path, monkeypatch, command, escape):
    root = tmp_path / "projeto"
    root.mkdir()
    secret = tmp_path / "fora.txt"
    secret.write_text("conteudo que nao pode sair")
    (root / "link").symlink_to(secret)
    monkeypatch.chdir(root)
    path = {"absolute": str(secret), "relative": "../fora.txt", "symlink": "link"}[escape]
    gateway = install(CanonicalToolCall("read", "read_file", json.dumps({"path": path})))
    assert main([command, "--session", "guard", "--tools", "leia"]) == 1
    result = next(m for m in gateway.requests[1].messages if m.role == "tool")
    assert "conteudo que nao pode sair" not in result.content[0].value
    assert "workspace" in result.content[0].value.lower()


def test_aprovacao_nao_libera_escrita_externa(install, tmp_path, monkeypatch):
    root = tmp_path / "projeto"
    root.mkdir()
    target = tmp_path / "fora.txt"
    target.write_text("original")
    monkeypatch.chdir(root)
    install(
        CanonicalToolCall(
            "write", "write_file", json.dumps({"path": "../fora.txt", "content": "alterado"})
        )
    )
    with terminal_input(monkeypatch, "sim\n"):
        assert main(["run", "--session", "guard", "--tools", "escreva"]) == 1
    assert target.read_text() == "original"


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    return registry


def test_workspace_explicito_e_caminhos_relativos(install, tmp_path, monkeypatch):
    root = tmp_path / "outro-projeto"
    root.mkdir()
    monkeypatch.chdir(tmp_path)
    gateway = install(
        CanonicalToolCall(
            "write", "write_file", json.dumps({"path": "sub/novo.txt", "content": "feito"})
        )
    )
    with terminal_input(monkeypatch, "sim\n"):
        assert (
            main(["run", "--session", "guard", "--tools", "--workspace", str(root), "escreva"]) == 0
        )
    assert (root / "sub/novo.txt").read_text() == "feito"
    assert not (tmp_path / "sub").exists()
    names = {tool["function"]["name"] for tool in gateway.requests[0].tools}
    assert "read_file" in names
    assert "bash" not in names
    assert "git" not in names
    assert not any(name.startswith("mcp__") for name in names)


@pytest.mark.parametrize(
    "tool,args",
    [
        ("edit_file", {"old_str": "original", "new_str": "alterado"}),
        ("patch", {"old_string": "original", "new_string": "alterado"}),
        ("write_file", {"content": "alterado"}),
        ("read_file", {}),
    ],
)
@pytest.mark.parametrize("kind", ["file_link", "directory_link", "hard_link", "prefix"])
def test_todos_os_handlers_recusam_alias_externo(tools, tmp_path, tool, args, kind):
    root = tmp_path / "projeto"
    root.mkdir()
    outside = tmp_path / "projeto-outro"
    outside.mkdir()
    source = outside / "alvo.txt"
    source.write_text("original")
    if kind == "file_link":
        (root / "link").symlink_to(source)
        path = "link"
    elif kind == "directory_link":
        (root / "link").symlink_to(outside, target_is_directory=True)
        path = "link/alvo.txt"
    elif kind == "hard_link":
        os.link(source, root / "link")
        path = "link"
    else:
        path = str(source)
    with fs.workspace_scope(root):
        result = tools.dispatch(tool, {"path": path, **args})
    assert "error" in result
    assert "workspace" in result["error"].lower()
    assert source.read_text() == "original"


@pytest.mark.parametrize("target", ["files", "content"])
def test_busca_nao_segue_links_e_lista_nao_expoe_metadados(tools, tmp_path, target):
    root = tmp_path / "projeto"
    root.mkdir()
    (root / "interno.txt").write_text("achado interno")
    outside = tmp_path / "externo.txt"
    outside.write_text("achado secreto")
    (root / "link.txt").symlink_to(outside)
    with fs.workspace_scope(root):
        listing = tools.dispatch("list_dir", {})
        result = tools.dispatch(
            "search_files", {"pattern": "*" if target == "files" else "achado", "target": target}
        )
    assert [entry["name"] for entry in listing["entries"]] == ["interno.txt"]
    assert [match["path"] for match in result["matches"]] == ["interno.txt"]
    assert "secreto" not in json.dumps(result)


def test_patch_valida_todos_os_arquivos_antes_da_primeira_escrita(tools, tmp_path):
    root = tmp_path / "projeto"
    root.mkdir()
    internal = root / "interno.txt"
    internal.write_text("original\n")
    outside = tmp_path / "externo.txt"
    outside.write_text("original\n")
    patch = dedent("""\
    <<<<<<< SEARCH interno.txt
    original
    =======
    alterado
    >>>>>>> REPLACE
    <<<<<<< SEARCH ../externo.txt
    original
    =======
    alterado
    >>>>>>> REPLACE
    """)
    with fs.workspace_scope(root):
        result = tools.dispatch("patch", {"mode": "patch", "patch": patch})
    assert "workspace" in result["error"].lower()
    assert internal.read_text() == outside.read_text() == "original\n"


def test_bridge_async_preserva_workspace_e_contexto_nao_vaza(tools, tmp_path):
    first = tmp_path / "primeiro"
    second = tmp_path / "segundo"
    first.mkdir()
    second.mkdir()
    (first / "arquivo.txt").write_text("primeiro")
    (second / "arquivo.txt").write_text("segundo")

    async def read(root):
        with fs.workspace_scope(root):
            await asyncio.sleep(0)
            assert tools.dispatch("read_file", {"path": "arquivo.txt"})["content"] == root.name
            if root == first:
                assert "error" in tools.dispatch("read_file", {"path": "../segundo/arquivo.txt"})

    async def run():
        await asyncio.gather(read(first), read(second))

    asyncio.run(run())
    assert not fs.restricted()
    assert (
        tools.dispatch("read_file", {"path": str(second / "arquivo.txt")})["content"] == "segundo"
    )


@pytest.mark.parametrize("write", [False, True])
@pytest.mark.parametrize("swap", ["file", "parent"])
def test_troca_de_link_entre_validacao_e_abertura_nao_escapa(tmp_path, monkeypatch, write, swap):
    root = tmp_path / "projeto"
    parent = root / "sub"
    parent.mkdir(parents=True)
    internal = parent / "arquivo.txt"
    internal.write_text("interno")
    outside = tmp_path / "fora"
    outside.mkdir()
    target = outside / "arquivo.txt"
    target.write_text("secreto")
    original_open = os.open
    changed = False

    def racing_open(path, flags, *args, **kwargs):
        nonlocal changed
        if not changed and path == ("arquivo.txt" if swap == "file" else "sub"):
            changed = True
            if swap == "file":
                internal.unlink()
                internal.symlink_to(target)
            else:
                parent.rename(root / "anterior")
                parent.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *args, **kwargs)

    with fs.workspace_scope(root):
        assert fs.file_path("sub/arquivo.txt") == internal
        monkeypatch.setattr(os, "open", racing_open)
        with pytest.raises(fs.WorkspaceDenied):
            if write:
                fs.write_text("sub/arquivo.txt", "alterado")
            else:
                fs.read_text("sub/arquivo.txt")
    assert changed
    assert target.read_text() == "secreto"


@pytest.mark.parametrize(
    "name", ["bash", "terminal", "git", "calendar", "mcp__servidor__ler", "extensao_futura"]
)
def test_ferramentas_sem_isolamento_nao_executam_handler(tools, tmp_path, name):
    effects = []
    tools.register(
        "bash" if name == "terminal" else name, lambda **_: effects.append("executou"), {}
    )
    with fs.workspace_scope(tmp_path):
        result = tools.dispatch(name, {})
    assert result["status"] == "denied"
    assert effects == []


def test_contexto_retido_nao_reutiliza_descritor_de_outro_turno(tmp_path):
    first = tmp_path / "primeiro"
    second = tmp_path / "segundo"
    first.mkdir()
    second.mkdir()
    (first / "arquivo.txt").write_text("primeiro")
    (second / "arquivo.txt").write_text("segundo")
    with fs.workspace_scope(first):
        stale = contextvars.copy_context()
    with fs.workspace_scope(second):
        with pytest.raises(fs.WorkspaceDenied):
            stale.run(fs.read_text, first / "arquivo.txt")
        assert fs.read_text("arquivo.txt") == "segundo"


def test_mudar_cwd_depois_de_iniciar_cli_nao_muda_workspace(install, tmp_path, monkeypatch):
    from kairos_cli import chat

    root = tmp_path / "projeto"
    other = tmp_path / "outro"
    root.mkdir()
    other.mkdir()
    (root / "arquivo.txt").write_text("projeto autorizado")
    (other / "arquivo.txt").write_text("outro projeto")
    gateway = install(CanonicalToolCall("read", "read_file", json.dumps({"path": "arquivo.txt"})))
    monkeypatch.chdir(root)
    build = chat.build_interaction_service

    def moving_build(home):
        monkeypatch.chdir(other)
        return build(home)

    monkeypatch.setattr(chat, "build_interaction_service", moving_build)
    assert main(["run", "--session", "guard", "--tools", "leia"]) == 0
    result = next(m for m in gateway.requests[1].messages if m.role == "tool")
    assert "projeto autorizado" in result.content[0].value
    assert "outro projeto" not in result.content[0].value


@pytest.mark.parametrize("kind", ["missing", "file", "without_tools"])
def test_workspace_invalido_recusado_antes_de_criar_servico(tmp_path, monkeypatch, kind):
    from kairos_cli import chat

    path = tmp_path / "raiz"
    if kind == "file":
        path.write_text("nao e diretorio")
    monkeypatch.setattr(
        chat, "build_interaction_service", lambda _: pytest.fail("serviço não deveria abrir")
    )
    args = ["run", "--session", "guard", "--workspace", str(path), "leia"]
    if kind != "without_tools":
        args.append("--tools")
    assert main(args) == 2


def test_raiz_trocada_por_link_antes_do_turno_falha_fechada(install, tmp_path, monkeypatch, capsys):
    from kairos_cli import chat

    root = tmp_path / "projeto"
    outside = tmp_path / "fora"
    root.mkdir()
    outside.mkdir()
    (outside / "arquivo.txt").write_text("segredo ficticio")
    gateway = install(CanonicalToolCall("read", "read_file", json.dumps({"path": "arquivo.txt"})))
    build = chat.build_interaction_service

    def replace_root(home):
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
        return build(home)

    monkeypatch.setattr(chat, "build_interaction_service", replace_root)
    assert main(["run", "--session", "guard", "--tools", "--workspace", str(root), "leia"]) == 1
    assert gateway.requests == []
    captured = capsys.readouterr()
    assert "Workspace indisponível" in captured.err
    assert "segredo ficticio" not in captured.out


def test_agenda_real_nao_altera_arquivo_externo(tools, tmp_path, monkeypatch):
    from kairos_tools.calendar import register_calendar_tool

    home = tmp_path / "home"
    root = tmp_path / "projeto"
    home.mkdir()
    root.mkdir()
    calendar = tmp_path / "externo.ics"
    calendar.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
    original = calendar.read_bytes()
    (home / "config.yaml").write_text(f"calendar:\n  source: {calendar}\n")
    monkeypatch.setenv("KAIROS_HOME", str(home))
    register_calendar_tool(tools)
    args = {
        "subcommand": "add",
        "titulo": "Evento ficticio",
        "inicio": "2026-10-06T10:00:00Z",
        "duracao_min": 30,
    }
    with fs.workspace_scope(root):
        assert tools.dispatch("calendar", args)["status"] == "denied"
    assert calendar.read_bytes() == original
    assert tools.dispatch("calendar", args)["success"]
    assert calendar.read_bytes() != original


def test_mcp_real_nao_inicia_processo_no_workspace(tools, tmp_path):
    from kairos_mcp.client import MCPServerConfig
    from kairos_tools.mcp_tools import _make_handler

    marker = tmp_path / "iniciou"
    server = tmp_path / "servidor.py"
    fake_server = Path(__file__).with_name("fake_mcp_server.py")
    server.write_text(
        "import runpy\nfrom pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('iniciou')\n"
        f"runpy.run_path({str(fake_server)!r}, run_name='__main__')\n"
    )
    config = MCPServerConfig(name="fake", command=sys.executable, args=(str(server),))
    tools.register("mcp__fake__sum", _make_handler(config, "sum"), {})
    with fs.workspace_scope(tmp_path):
        assert tools.dispatch("mcp__fake__sum", {"a": 2, "b": 3})["status"] == "denied"
    assert not marker.exists()
    assert tools.dispatch("mcp__fake__sum", {"a": 2, "b": 3}) == "5"
    assert marker.read_text() == "iniciou"


def test_override_de_plugin_nao_contorna_workspace(tools, tmp_path):
    effects = []
    tools.apply_plugin_override(
        "read_file", lambda **_: effects.append("executou"), plugin="externo", generation=1
    )
    with fs.workspace_scope(tmp_path):
        result = tools.dispatch("read_file", {"path": "arquivo.txt"})
    assert result["status"] == "denied"
    assert effects == []
