"""CLI, serviço canônico e efeitos reais em arquivos temporários."""

import asyncio
import io
import json
import os
import pty
import sys
from contextlib import contextmanager

import pytest
from test_chat_search_loop import RoundGateway, answer_round, make_service
from test_chat_tool_approval import mutator_round

from kairos_cli import chat
from kairos_cli.main import main
from kairos_providers import CanonicalToolCall
from kairos_state import connect, initialize_schema


@contextmanager
def terminal_input(monkeypatch, text):
    master, slave = pty.openpty()
    try:
        with os.fdopen(slave, "r") as stream:
            monkeypatch.setattr("sys.stdin", stream)
            if text:
                os.write(master, text.encode())
            yield
    finally:
        os.close(master)


@pytest.fixture
def install(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)

    def build(call):
        gateway = RoundGateway([mutator_round(call), answer_round()])
        service = make_service(connection, gateway)

        class Client:
            stream = service.stream
            decide_tool_approval = service.decide_tool_approval

            async def aclose(self):
                pass

        monkeypatch.setattr(chat, "build_interaction_service", lambda _: Client())
        return gateway

    yield build
    connection.close()


def write_call(target):
    return CanonicalToolCall(
        "write", "write_file", json.dumps({"path": str(target), "content": "feito"})
    )


@pytest.mark.parametrize("command", ["run", "chat"])
def test_cli_le_arquivo_pelo_core(install, tmp_path, capsys, command):
    source = tmp_path / "fonte.txt"
    source.write_text("conteudo real")
    gateway = install(CanonicalToolCall("read", "read_file", json.dumps({"path": str(source)})))
    assert main([command, "--session", "cli-test", "--tools", "leia"]) == 0
    result = next(m for m in gateway.requests[1].messages if m.role == "tool")
    assert "conteudo real" in result.content[0].value
    assert "read_file" in capsys.readouterr().err


def test_cli_bash_no_host_nao_e_habilitado_no_workspace(install, monkeypatch, capsys):
    gateway = install(CanonicalToolCall("shell", "bash", json.dumps({"command": "exit 7"})))
    with terminal_input(monkeypatch, "sim\n"):
        assert main(["run", "--session", "cli-test", "--tools", "execute"]) != 0
    result = next(m for m in gateway.requests[1].messages if m.role == "tool")
    assert "não habilitada" in result.content[0].value
    assert "bash" not in {tool["function"]["name"] for tool in gateway.requests[0].tools}
    assert "falhou" in capsys.readouterr().err


@pytest.mark.parametrize(
    "answer,allowed", [("sim\n", True), ("não\n", False), ("\n", False), ("\x04", False)]
)
def test_cli_aprova_ou_recusa_escrita_real(install, tmp_path, monkeypatch, answer, allowed):
    target = tmp_path / "resultado.txt"
    install(write_call(target))
    with terminal_input(monkeypatch, answer):
        code = main(["run", "--session", "cli-test", "--tools", "escreva"])
    assert target.exists() is allowed
    if allowed:
        assert target.read_text() == "feito"
    assert code == (0 if allowed else 1)


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_sem_terminal_recusa_sem_consumir_stdin(
    install, tmp_path, monkeypatch, capsys, as_json
):
    target = tmp_path / "nao-escrever.txt"
    install(write_call(target))
    stdin = io.StringIO("sim\n")
    monkeypatch.setattr("sys.stdin", stdin)
    args = ["run", "--session", "cli-test", "--tools", "escreva"]
    if as_json:
        args.append("--json")
    assert main(args) == 1
    assert not target.exists()
    assert stdin.tell() == 0
    output = capsys.readouterr()
    if as_json:
        events = [json.loads(line) for line in output.out.splitlines()]
        assert any(e["type"] == "tool_approval_request" for e in events)
        assert events[-1]["type"] == "turn_end"
    assert "recusada" in output.err.lower()


def test_cli_interativa_preserva_tools(install, tmp_path, monkeypatch):
    source = tmp_path / "fonte.txt"
    source.write_text("interativo")
    gateway = install(CanonicalToolCall("read", "read_file", json.dumps({"path": str(source)})))
    inputs = iter(["leia", "sair"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert main(["chat", "--session", "cli-test", "--tools"]) == 0
    assert any(
        m.role == "tool" and "interativo" in m.content[0].value
        for m in gateway.requests[1].messages
    )


def test_cli_runtime_recusa_tools_antes_de_abrir_servico(monkeypatch, tmp_path):
    monkeypatch.setattr(chat, "_is_runtime_session", lambda *_: True)
    monkeypatch.setattr(chat, "build_interaction_service", lambda _: pytest.fail("abriu serviço"))
    with pytest.raises(chat.ChatUsageError, match="--tools"):
        asyncio.run(
            chat.run_chat(
                home=tmp_path,
                session_id="runtime",
                prompt="oi",
                provider=None,
                model=None,
                as_json=False,
                tools=True,
            )
        )


def test_aprovacao_expira_sem_bloquear_loop(monkeypatch):
    from kairos_cli import tool_approval
    from kairos_integration import InteractionEvent

    monkeypatch.setattr(tool_approval, "TOOL_APPROVAL_TIMEOUT_SECONDS", 0.02)
    event = InteractionEvent.tool_approval_request("approval", write_call("arquivo"), "session")

    async def scenario():
        decision = await asyncio.wait_for(
            tool_approval.request_tool_decision(event, as_json=False), 1
        )
        assert decision == "deny"

    with terminal_input(monkeypatch, ""):
        asyncio.run(scenario())


def test_aprovacao_cancelada_libera_terminal(monkeypatch):
    from kairos_cli import tool_approval
    from kairos_integration import InteractionEvent

    event = InteractionEvent.tool_approval_request("approval", write_call("arquivo"), "session")

    async def scenario():
        pending = asyncio.create_task(tool_approval.request_tool_decision(event, as_json=False))
        await asyncio.sleep(0.01)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not asyncio.get_running_loop().remove_reader(sys.stdin.fileno())

    with terminal_input(monkeypatch, ""):
        asyncio.run(scenario())
