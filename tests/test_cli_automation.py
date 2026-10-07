"""Autorizações explícitas da CLI com efeitos reais e provedor fictício."""

import asyncio
import io
import json

import pytest
from test_cli_tools import install as cli_install

from kairos_cli import chat
from kairos_cli.main import main
from kairos_providers import CanonicalToolCall

install = cli_install


@pytest.mark.parametrize("command", ["run", "chat"])
@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize("tool", ["write_file", "edit_file", "patch"])
def test_autorizacao_explicita_executa_sem_ler_stdin(
    install, tmp_path, monkeypatch, capsys, *, command, as_json, tool
):
    target = tmp_path / "alvo.txt"
    target.write_text("original")
    args = {
        "write_file": {"path": "alvo.txt", "content": "alterado"},
        "edit_file": {"path": "alvo.txt", "old_str": "original", "new_str": "alterado"},
        "patch": {"path": "alvo.txt", "old_string": "original", "new_string": "alterado"},
    }[tool]
    install(CanonicalToolCall("mutator", tool, json.dumps(args)))
    stdin = io.StringIO("nao consumir\n")
    monkeypatch.setattr("sys.stdin", stdin)
    options = [
        command,
        "--session",
        "automation",
        "--tools",
        "--workspace",
        str(tmp_path),
        "--allow-tool",
        tool,
        "altere",
    ]
    if as_json:
        options.append("--json")
    assert main(options) == 0
    assert target.read_text() == "alterado"
    assert stdin.tell() == 0
    output = capsys.readouterr()
    assert tool in output.err
    assert "alterado" not in output.err
    if as_json:
        events = [json.loads(line) for line in output.out.splitlines()]
        assert any(event["type"] == "tool_approval_request" for event in events)
        assert events[-1]["type"] == "turn_end"


@pytest.mark.parametrize("escape", ["../fora.txt", "link"])
def test_autorizacao_nao_amplia_workspace(install, tmp_path, monkeypatch, escape):
    root = tmp_path / "projeto"
    root.mkdir()
    outside = tmp_path / "fora.txt"
    outside.write_text("original")
    (root / "link").symlink_to(outside)
    install(
        CanonicalToolCall(
            "write", "write_file", json.dumps({"path": escape, "content": "alterado"})
        )
    )
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert (
        main(
            [
                "run",
                "--session",
                "automation",
                "--tools",
                "--workspace",
                str(root),
                "--allow-tool",
                "write_file",
                "altere",
            ]
        )
        == 1
    )
    assert outside.read_text() == "original"


def test_autorizacao_nao_persiste_para_proxima_invocacao(install, tmp_path, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO())

    def call(path):
        return CanonicalToolCall(
            "write", "write_file", json.dumps({"path": path, "content": "feito"})
        )

    install(call("primeiro.txt"))
    options = ["run", "--session", "automation", "--tools", "--workspace", str(tmp_path), "escreva"]
    assert main([*options, "--allow-tool", "write_file"]) == 0
    install(call("segundo.txt"))
    assert main(options) == 1
    assert (tmp_path / "primeiro.txt").read_text() == "feito"
    assert not (tmp_path / "segundo.txt").exists()


@pytest.mark.parametrize("allow_edit", [False, True])
def test_lista_explicita_e_aplicada_a_cada_chamada(install, tmp_path, monkeypatch, allow_edit):
    install(
        CanonicalToolCall("write", "write_file", '{"path":"alvo.txt","content":"primeiro"}'),
        CanonicalToolCall(
            "edit", "edit_file", '{"path":"alvo.txt","old_str":"primeiro","new_str":"segundo"}'
        ),
    )
    monkeypatch.setattr("sys.stdin", io.StringIO("sim\n"))
    options = [
        "run",
        "--session",
        "automation",
        "--tools",
        "--workspace",
        str(tmp_path),
        "--allow-tool",
        "write_file",
        "altere",
    ]
    if allow_edit:
        options.extend(["--allow-tool", "edit_file"])
    assert main(options) == (0 if allow_edit else 1)
    assert (tmp_path / "alvo.txt").read_text() == ("segundo" if allow_edit else "primeiro")


@pytest.mark.parametrize("missing", ["tools", "workspace", "prompt", "runtime"])
def test_autorizacao_incompleta_recusa_antes_de_compor_servico(tmp_path, monkeypatch, missing):
    monkeypatch.setattr(chat, "build_interaction_service", lambda _: pytest.fail("abriu serviço"))
    monkeypatch.setattr(chat, "_is_runtime_session", lambda *_: missing == "runtime")
    with pytest.raises(chat.ChatUsageError, match="--allow-tool"):
        asyncio.run(
            chat.run_chat(
                home=tmp_path,
                session_id="automation",
                prompt="" if missing == "prompt" else "escreva",
                provider=None,
                model=None,
                as_json=True,
                tools=missing != "tools",
                workspace=None if missing == "workspace" else str(tmp_path),
                allow_tools=["write_file"],
            )
        )


@pytest.mark.parametrize(
    "name",
    [
        "*",
        "write_*",
        "write_file,edit_file",
        "read_file",
        "Write_file",
        "mcp__fake__sum",
        "git",
        "calendar",
        "terminal",
        "desconhecida",
    ],
)
def test_nomes_nao_autorizaveis_sao_recusados(tmp_path, monkeypatch, name):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(chat, "build_interaction_service", lambda _: pytest.fail("abriu serviço"))
    assert (
        main(
            [
                "run",
                "--session",
                "automation",
                "--tools",
                "--workspace",
                str(tmp_path),
                "--allow-tool",
                name,
                "escreva",
            ]
        )
        == 2
    )


def test_lista_do_chamador_e_congelada_antes_de_compor(install, tmp_path, monkeypatch):
    target = tmp_path / "alvo.txt"
    target.write_text("original")
    install(
        CanonicalToolCall(
            "edit", "edit_file", '{"path":"alvo.txt","old_str":"original","new_str":"alterado"}'
        )
    )
    names = ["write_file"]
    build = chat.build_interaction_service

    def mutating_build(home):
        names.append("edit_file")
        return build(home)

    monkeypatch.setattr(chat, "build_interaction_service", mutating_build)
    monkeypatch.setattr("sys.stdin", io.StringIO())
    code = asyncio.run(
        chat.run_chat(
            home=tmp_path,
            session_id="automation",
            prompt="altere",
            provider=None,
            model=None,
            as_json=True,
            tools=True,
            workspace=str(tmp_path),
            allow_tools=names,
        )
    )
    assert code == 1
    assert target.read_text() == "original"


def test_autorizacao_bash_nao_libera_shell_do_host(install, tmp_path, monkeypatch):
    marker = tmp_path / "nao-executar"
    install(CanonicalToolCall("shell", "bash", json.dumps({"command": f"touch {marker}"})))
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert (
        main(
            [
                "run",
                "--session",
                "automation",
                "--tools",
                "--workspace",
                str(tmp_path),
                "--allow-tool",
                "bash",
                "execute",
            ]
        )
        == 1
    )
    assert not marker.exists()
