"""Sandbox do turno comum: `bash`/`terminal` no worker atestado (D-RT.4)."""

import asyncio
import json
from pathlib import Path

import pytest
from test_chat_search_loop import RoundGateway, answer_round, make_service, turn
from test_chat_tool_approval import bash_call, collect_with, mutator_round, wait_for_kind

from kairos_integration.chat_sandbox import (
    WORKSPACE,
    ChatBashSandbox,
    ChatSandboxUnavailable,
    parse_chat_sandbox_config,
)
from kairos_integration.chat_tools import execute_chat_tool
from kairos_integration.composition import build_chat_sandbox


class DoubleWorker:
    """Worker atestado de mentira: registra chamadas sem tocar Docker."""

    def __init__(self, result=None, *, exec_error=None):
        self.result = result or {"exitCode": 0, "stdout": "ok", "stderr": ""}
        self.exec_error = exec_error
        self.entered = False
        self.closed = False
        self.calls = []

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_exc):
        await self.aclose()

    async def execute(self, command, *, cwd, limits):
        self.calls.append({"command": command, "cwd": cwd, "limits": limits})
        if self.exec_error is not None:
            await self.aclose()
            raise self.exec_error
        if self.result["exitCode"] != 0:
            await self.aclose()
        return self.result

    async def aclose(self):
        self.closed = True


def worker_factory(recording):
    async def factory(_project: Path):
        worker = DoubleWorker()
        recording.append(worker)
        return worker

    return factory


@pytest.fixture
def db(tmp_path):
    from kairos_state import connect, initialize_schema

    connection = connect(tmp_path / "state.db")
    initialize_schema(connection)
    yield connection
    connection.close()


def with_code_execution(home, timeout):
    (home / "config.yaml").write_text(
        f"code_execution:\n  timeout_seconds: {timeout}\n", encoding="utf-8"
    )
    return home


def test_parse_sandbox_config_fail_closed():
    assert parse_chat_sandbox_config(None) is False
    assert parse_chat_sandbox_config(True) is True
    assert parse_chat_sandbox_config(False) is False
    for bad in ("true", 1, 1.0, []):
        with pytest.raises(ValueError):
            parse_chat_sandbox_config(bad)


def test_bash_runs_on_worker_with_argv_cwd_and_limits(tmp_path):
    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory(homeowners))
    result = asyncio.run(
        sandbox.dispatch(
            "conv",
            "bash",
            {"command": "echo oi", "cwd": "/workspace/sub", "timeout": 5},
        )
    )
    assert result == {
        "stdout": "ok",
        "stderr": "",
        "exit_code": 0,
        "success": True,
    }
    assert len(homeowners) == 1 and homeowners[0].entered
    (call,) = homeowners[0].calls
    assert call["command"] == ["/bin/sh", "-c", "echo oi"]
    assert call["cwd"] == "/workspace/sub"
    assert call["limits"].timeout_seconds == 5


def test_terminal_alias_routes_to_bash(tmp_path):
    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory(homeowners))
    asyncio.run(sandbox.dispatch("conv", "terminal", {"command": "ls"}))
    (call,) = homeowners[0].calls
    assert call["command"] == ["/bin/sh", "-c", "ls"]


def test_bash_timeout_capped_by_code_execution_ceiling(tmp_path):
    home = with_code_execution(tmp_path, 10)
    homeowners = []
    sandbox = ChatBashSandbox(home, worker_factory=worker_factory(homeowners))
    asyncio.run(sandbox.dispatch("conv", "bash", {"command": "date", "timeout": 120}))
    asyncio.run(sandbox.dispatch("conv", "bash", {"command": "date"}))
    assert [call["limits"].timeout_seconds for call in homeowners[0].calls] == [10, 10]


def test_non_bash_delegates_to_host_registry_without_worker(tmp_path, monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        "kairos_integration.chat_sandbox.registry.dispatch",
        lambda name, arguments=None: dispatched.append((name, arguments)) or {"status": "ok"},
    )
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory([]))
    value = asyncio.run(sandbox.dispatch("conv", "read_file", {"path": "x"}))
    assert value == {"status": "ok"}
    assert dispatched == [("read_file", {"path": "x"})]


def test_one_worker_per_conversation_reused_and_closed(tmp_path):
    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory(homeowners))
    for _ in range(2):
        asyncio.run(sandbox.dispatch("conv-a", "bash", {"command": "echo a"}))
    asyncio.run(sandbox.dispatch("conv-b", "bash", {"command": "echo b"}))
    asyncio.run(sandbox.aclose())
    assert len(homeowners) == 2
    assert len(homeowners[0].calls) == 2 and len(homeowners[1].calls) == 1
    assert all(worker.closed for worker in homeowners)


def test_nonzero_exit_discards_worker_and_shapes_error(tmp_path):
    def failing_worker_factory(recording):
        async def factory(_project):
            worker = DoubleWorker({"exitCode": 2, "stdout": "erro", "stderr": "boom"})
            recording.append(worker)
            return worker

        return factory

    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=failing_worker_factory(homeowners))
    result = asyncio.run(sandbox.dispatch("conv", "bash", {"command": "false"}))
    assert result["success"] is False and result["exit_code"] == 2
    assert result["stderr"] == "boom"
    asyncio.run(sandbox.dispatch("conv", "bash", {"command": "date"}))
    assert len(homeowners) == 2, "worker após saída não-zero não é reutilizado"


def test_factory_failure_is_unavailable_and_never_cached(tmp_path):
    attempts = []

    async def failing_factory(_project):
        attempts.append(1)
        raise RuntimeError("sem docker")

    sandbox = ChatBashSandbox(tmp_path, worker_factory=failing_factory)
    with pytest.raises(ChatSandboxUnavailable):
        asyncio.run(sandbox.dispatch("conv", "bash", {"command": "echo"}))
    with pytest.raises(ChatSandboxUnavailable):
        asyncio.run(sandbox.dispatch("conv", "bash", {"command": "echo"}))
    assert len(attempts) == 2, "worker falho não é memorizado"


def test_execute_failure_is_unavailable_and_discards_worker(tmp_path):
    homeowners = []
    produced = {"n": 0}

    async def dying_factory(_project):
        produced["n"] += 1
        worker = DoubleWorker()
        if produced["n"] == 1:
            worker.exec_error = RuntimeError("container morreu")
        homeowners.append(worker)
        return worker

    sandbox = ChatBashSandbox(tmp_path, worker_factory=dying_factory)
    with pytest.raises(ChatSandboxUnavailable):
        asyncio.run(sandbox.dispatch("conv", "bash", {"command": "echo"}))
    assert homeowners[0].closed
    result = asyncio.run(sandbox.dispatch("conv", "bash", {"command": "echo"}))
    assert result["success"] is True
    assert len(homeowners) == 2, "worker morto não é reutilizado"


def test_cwd_outside_workspace_is_invalid_argument(tmp_path):
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory([]))
    with pytest.raises(TypeError):
        asyncio.run(sandbox.dispatch("conv", "bash", {"command": "pwd", "cwd": "/etc"}))


def test_cwd_normalizes_within_workspace(tmp_path):
    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory(homeowners))
    asyncio.run(sandbox.dispatch("conv", "bash", {"command": "pwd", "cwd": f"{WORKSPACE}//sub"}))
    (call,) = homeowners[0].calls
    assert call["cwd"] == f"{WORKSPACE}/sub"


def test_execute_chat_tool_maps_unavailable_without_host_fallback(tmp_path, monkeypatch):
    async def failing_factory(_project):
        raise RuntimeError("sem docker")

    monkeypatch.setattr(
        "kairos_integration.chat_sandbox.registry.dispatch",
        lambda *a, **k: pytest.fail("host dispatch não pode ser o plano B"),
        raising=False,
    )
    sandbox = ChatBashSandbox(tmp_path, worker_factory=failing_factory)

    async def dispatch(name, arguments=None):
        return await sandbox.dispatch("conv", name, arguments)

    result = asyncio.run(execute_chat_tool(bash_call("b1"), execute=dispatch))
    assert result.is_error
    assert json.loads(result.content)["status"] == "unavailable"


def test_common_turn_approved_bash_runs_in_sandbox(db, tmp_path):
    homeowners = []
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory(homeowners))
    service = make_service(
        db, RoundGateway([mutator_round(bash_call("b1")), answer_round()]), chat_sandbox=sandbox
    )
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="allow"
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    assert events[-1].kind == "turn_end"
    assert len(homeowners) == 1
    (call,) = homeowners[0].calls
    assert call["command"] == ["/bin/sh", "-c", "echo oi"]
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and not results[0].is_error


def test_unavailable_sandbox_never_falls_back_to_host(db, tmp_path, monkeypatch):
    async def failing_factory(_project):
        raise RuntimeError("sem docker")

    monkeypatch.setattr(
        "kairos_integration.chat_sandbox.registry.dispatch",
        lambda *a, **k: pytest.fail("host dispatch não pode ser o plano B"),
        raising=False,
    )
    monkeypatch.setattr(
        "kairos_integration.interaction_service.registry.dispatch",
        lambda *a, **k: pytest.fail("host dispatch não pode ser o plano B"),
        raising=False,
    )
    sandbox = ChatBashSandbox(tmp_path, worker_factory=failing_factory)
    service = make_service(
        db, RoundGateway([mutator_round(bash_call("b1")), answer_round()]), chat_sandbox=sandbox
    )
    events = []

    async def scenario():
        driver = asyncio.create_task(collect_with(events, service, turn(tools=True)))
        approval = await wait_for_kind(events, "tool_approval_request")
        service.decide_tool_approval(
            approval_id=approval.tool_approval_id, session_id="search", decision="allow"
        )
        await asyncio.wait_for(driver, 5)

    asyncio.run(scenario())
    results = [e.tool_result for e in events if e.kind == "tool_result"]
    assert len(results) == 1 and results[0].is_error
    assert json.loads(results[0].content)["status"] == "unavailable"


def test_build_chat_sandbox_gates_on_config(tmp_path):
    assert build_chat_sandbox(tmp_path, {}) is None
    assert build_chat_sandbox(tmp_path, {"chat": {}}) is None
    assert build_chat_sandbox(tmp_path, {"chat": {"sandboxed_bash": False}}) is None
    with pytest.raises(ValueError):
        build_chat_sandbox(tmp_path, {"chat": "não-mapeamento"})
    with pytest.raises(ValueError):
        build_chat_sandbox(tmp_path, {"chat": {"sandboxed_bash": "true"}})
    sandbox = build_chat_sandbox(tmp_path, {"chat": {"sandboxed_bash": True}})
    assert isinstance(sandbox, ChatBashSandbox)
    asyncio.run(sandbox.aclose())


def test_bash_requires_command_argument(tmp_path):
    sandbox = ChatBashSandbox(tmp_path, worker_factory=worker_factory([]))
    with pytest.raises(TypeError):
        asyncio.run(sandbox.dispatch("conv", "bash", {"command": ""}))
    with pytest.raises(TypeError):
        asyncio.run(sandbox.dispatch("conv", "bash", {}))
