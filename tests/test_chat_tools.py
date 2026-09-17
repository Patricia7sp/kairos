"""Chat's only tool executes bounded searches without host-tool dispatch."""

import asyncio
import json

import httpx
import pytest

from kairos_integration.interaction_contract import InteractionToolResult
from kairos_providers.adapter_contract import CanonicalToolCall
from kairos_tools import builtin


@pytest.fixture
def chat_tools():
    from kairos_integration import chat_tools

    return chat_tools


def call(arguments, *, name="web_search"):
    return CanonicalToolCall(id="search-123", name=name, arguments=arguments)


def upstream(monkeypatch, *, body=None, failure=None):
    client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        if failure is not None:
            raise failure
        return httpx.Response(200, text=body, headers={"Content-Type": "text/html"})

    monkeypatch.setattr(
        builtin.httpx,
        "AsyncClient",
        lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    return requests


def test_search_runs_real_http_parser_and_bounds_result_count(chat_tools, monkeypatch):
    body = """<html><body>
    <div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide">Guide <b>&amp; reference</b></a>
    <a class="result__snippet">Useful <b>source</b>.</a></div>
    <div class="result"><a class="result__a" href="https://example.net">Second</a></div>
    </body></html>"""
    requests = upstream(monkeypatch, body=body)
    result = asyncio.run(
        chat_tools.execute_web_search(call('{"query":"Kairos & tools","max_results":1}'))
    )
    assert result.tool_call_id == "search-123"
    assert result.is_error is False
    content = json.loads(result.content)
    assert content["status"] == "ok"
    assert content["results"] == [
        {
            "title": "Guide & reference",
            "url": "https://example.org/guide",
            "snippet": "Useful source.",
        }
    ]
    assert content["untrusted"] is True
    assert len(requests) == 1
    assert requests[0].url.host == "html.duckduckgo.com"
    assert requests[0].url.params["q"] == "Kairos & tools"


@pytest.mark.parametrize("name", ["bash", "read_file", "write_file", "list_dir", "unknown"])
def test_unknown_tools_have_no_network_or_host_effect(chat_tools, monkeypatch, tmp_path, name):
    sentinel = tmp_path / "should-not-exist"
    requests = upstream(monkeypatch, body="")
    result = asyncio.run(
        chat_tools.execute_web_search(
            call(json.dumps({"command": f"touch {sentinel}", "path": str(sentinel)}), name=name)
        )
    )
    assert result.is_error is True
    assert json.loads(result.content)["error"]
    assert requests == []
    assert not sentinel.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        "",
        "{",
        "[]",
        "null",
        "true",
        '"query"',
        "{}",
        '{"query":null}',
        '{"query":12}',
        '{"query":true}',
        '{"query":""}',
        '{"query":"   "}',
        json.dumps({"query": "a" * 2001}),
        '{"query":"test","max_results":0}',
        '{"query":"test","max_results":6}',
        '{"query":"test","max_results":true}',
        '{"query":"test","max_results":1.0}',
        '{"query":"test","max_results":"2"}',
        '{"query":"test","max_results":null}',
        '{"query":"test","command":"echo forbidden"}',
        '{"query":"test","query":"duplicate"}',
        '{"query":"test"}' + " " * 16_384,
        '{"query":"' + "é" * 9000 + '"}',
        "[" * 1100 + "]" * 1100,
        '{"query":"\ud800"}',
    ],
    ids=[
        "empty",
        "broken-json",
        "array",
        "null",
        "boolean",
        "string",
        "missing-query",
        "null-query",
        "numeric-query",
        "boolean-query",
        "empty-query",
        "blank-query",
        "long-query",
        "zero-limit",
        "high-limit",
        "boolean-limit",
        "float-limit",
        "string-limit",
        "null-limit",
        "extra-property",
        "duplicate-query",
        "oversized-json",
        "oversized-utf8",
        "deep-json",
        "invalid-unicode",
    ],
)
def test_invalid_arguments_never_send_requests(chat_tools, monkeypatch, arguments):
    requests = upstream(monkeypatch, body="")
    result = asyncio.run(chat_tools.execute_web_search(call(arguments)))
    assert result.is_error is True
    assert json.loads(result.content)["status"] == "invalid_arguments"
    assert requests == []


def test_default_limit_and_maximum_query_are_accepted(chat_tools, monkeypatch):
    body = (
        "<html>"
        + "".join(
            f'<a class="result__a" href="https://example.org/{index}">Source {index}</a>'
            for index in range(8)
        )
        + "</html>"
    )
    requests = upstream(monkeypatch, body=body)
    result = asyncio.run(chat_tools.execute_web_search(call(json.dumps({"query": "a" * 2000}))))
    assert not result.is_error
    assert len(json.loads(result.content)["results"]) == 5
    assert requests[0].url.params["q"] == "a" * 2000


def test_empty_results_remain_successful(chat_tools, monkeypatch):
    upstream(monkeypatch, body='<html><div class="no-results">No results found</div></html>')
    result = asyncio.run(chat_tools.execute_web_search(call('{"query":"no match"}')))
    assert not result.is_error
    assert json.loads(result.content)["results"] == []


@pytest.mark.parametrize("mode", ["builtin_error", "raised_error"])
def test_failures_never_expose_exception_details(chat_tools, monkeypatch, mode):
    failure_detail = "private upstream diagnostic"
    if mode == "builtin_error":
        upstream(monkeypatch, failure=httpx.ConnectError(failure_detail))
    else:

        async def fail(**kwargs):
            raise RuntimeError(failure_detail)

        monkeypatch.setattr(builtin, "web_search_tool", fail)
    result = asyncio.run(chat_tools.execute_web_search(call('{"query":"test"}')))
    assert result.is_error
    assert json.loads(result.content)["status"] == "unavailable"
    assert failure_detail not in result.content


def test_oversized_result_is_bounded_valid_json(chat_tools, monkeypatch):
    body = '<a class="result__a" href="https://example.org">' + "é" * 20_000 + "</a>"
    upstream(monkeypatch, body=body)
    result = asyncio.run(chat_tools.execute_web_search(call('{"query":"test"}')))
    assert len(result.content.encode("utf-8")) <= 32_768
    content = json.loads(result.content)
    assert result.is_error
    assert content["status"] == "output_too_large"


def test_timeout_cancels_search_and_returns_safe_error(chat_tools, monkeypatch):
    cancelled = []

    async def never_finishes(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    monkeypatch.setattr(builtin, "web_search_tool", never_finishes)
    monkeypatch.setattr(chat_tools, "SEARCH_TIMEOUT_SECONDS", 0.01)
    result = asyncio.run(chat_tools.execute_web_search(call('{"query":"test"}')))
    assert result.is_error
    assert json.loads(result.content)["status"] == "timeout"
    assert cancelled == [True]


def test_caller_cancellation_propagates_and_stops_search(chat_tools, monkeypatch):
    async def scenario():
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def search(**kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        monkeypatch.setattr(builtin, "web_search_tool", search)
        task = asyncio.create_task(chat_tools.execute_web_search(call('{"query":"test"}')))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()

    asyncio.run(scenario())


def test_definitions_reach_accepted_chat_tools_from_the_registry(chat_tools):
    names = {_definition_name(definition) for definition in chat_tools.chat_tool_definitions()}
    assert names == set(chat_tools.CHAT_TOOLS)
    assert "web_search" in names
    assert names <= set(chat_tools.CHAT_TOOLS)


def _definition_name(definition):
    return definition["function"]["name"]


def test_definitions_exclude_web_search_when_disabled(chat_tools):
    names = {
        _definition_name(definition)
        for definition in chat_tools.chat_tool_definitions(web_search_enabled=False)
    }
    assert "web_search" not in names
    assert "bash" in names and "read_file" in names


def test_definitions_drop_everything_outside_the_chat_allowlist(chat_tools):
    foreign = {"function": {"name": "runtime_port_forward", "parameters": {}}}
    names = {
        _definition_name(definition)
        for definition in chat_tools.chat_tool_definitions(definitions=[foreign])
    }
    assert names == set()


def test_mutating_tools_require_approval_read_only_do_not(chat_tools):
    assert all(chat_tools.needs_tool_approval(name) for name in chat_tools.MUTATING_TOOLS)
    for name in ("read_file", "list_dir", "search_files", "web_extract", "web_search"):
        assert not chat_tools.needs_tool_approval(name)


def test_git_approval_distingue_subcomando(chat_tools):
    assert not chat_tools.needs_tool_approval("git", {"subcommand": "status"})
    assert not chat_tools.needs_tool_approval("git", {"subcommand": "diff"})
    assert not chat_tools.needs_tool_approval("git", {"subcommand": "log"})
    assert chat_tools.needs_tool_approval("git", {"subcommand": "commit"})
    assert chat_tools.needs_tool_approval("git", '{"subcommand":"commit","message":"x"}')
    # Sem argumentos, nada é mutação — o default é recusar comprometido? Não:
    # sem subcomando o dispatch valida e recusa, mas não há aprovação pedida.
    assert not chat_tools.needs_tool_approval("git")


def test_denied_result_is_a_safe_error(chat_tools):
    result = chat_tools.denied_tool_result(call("{}", name="bash"))
    assert result.tool_call_id == "search-123"
    assert result.is_error is True
    assert json.loads(result.content) == {
        "status": "denied",
        "error": "Execução recusada pelo usuário.",
        "results": [],
    }


def test_generic_executor_rejects_unsupported_tool_without_execution(chat_tools):
    executed = []

    def dispatch(name, arguments):
        executed.append(name)
        return name

    result = asyncio.run(
        chat_tools.execute_chat_tool(call("{}", name="runtime_port_forward"), execute=dispatch)
    )
    assert executed == []
    assert result.is_error
    assert json.loads(result.content)["status"] == "unsupported_tool"


@pytest.mark.parametrize(
    "arguments",
    [
        "",
        "{",
        "[]",
        "null",
        '{"command":"ls","command":"ls"}',
        "{" + " " * 16_384 + "}",
    ],
    ids=["empty", "broken-json", "array", "null", "duplicate", "oversized"],
)
def test_generic_executor_rejects_invalid_argument_bodies(chat_tools, arguments):
    executed = []

    def dispatch(name, args):
        executed.append((name, args))
        return "ran"

    result = asyncio.run(
        chat_tools.execute_chat_tool(call(arguments, name="bash"), execute=dispatch)
    )
    assert executed == []
    assert json.loads(result.content)["status"] == "invalid_arguments"


def test_generic_executor_forwards_structurally_valid_bodies_to_the_handler(chat_tools):
    seen = []

    def dispatch(name, arguments):
        seen.append(arguments)
        return {"error": "comando não suportado pelo handler"}

    for raw in ("{}", '{"command":true}', '{"command":null}'):
        result = asyncio.run(chat_tools.execute_chat_tool(call(raw, name="bash"), execute=dispatch))
        assert result.is_error
    assert seen == [{}, {"command": True}, {"command": None}]


def test_generic_executor_dispatches_and_serializes_plain_output(chat_tools):
    seen = {}

    def dispatch(name, arguments):
        seen.update(arguments)
        return arguments

    result = asyncio.run(
        chat_tools.execute_chat_tool(
            call(json.dumps({"command": "ls", "path": "/tmp"}), name="bash"), execute=dispatch
        )
    )
    assert not result.is_error
    assert json.loads(result.content) == {"command": "ls", "path": "/tmp"}
    assert seen == {"command": "ls", "path": "/tmp"}


def test_generic_executor_awaits_async_output(chat_tools):
    async def dispatch(name, arguments):
        return {"ok": True}

    result = asyncio.run(
        chat_tools.execute_chat_tool(call('{"command":"ls"}', name="bash"), execute=dispatch)
    )
    assert json.loads(result.content) == {"ok": True}


def test_generic_executor_flags_dict_error_without_raising(chat_tools):
    def dispatch(name, arguments):
        return {"error": "permissão negada"}

    result = asyncio.run(
        chat_tools.execute_chat_tool(call('{"command":"ls"}', name="bash"), execute=dispatch)
    )
    assert result.is_error
    assert json.loads(result.content) == {"error": "permissão negada"}


def test_generic_executor_caps_oversized_output(chat_tools):
    def dispatch(name, arguments):
        return "x" * (chat_tools.MAX_OUTPUT_BYTES + 1)

    result = asyncio.run(
        chat_tools.execute_chat_tool(call('{"command":"ls"}', name="bash"), execute=dispatch)
    )
    assert result.is_error
    assert json.loads(result.content)["status"] == "output_too_large"


def test_generic_executor_failure_is_never_leaked(chat_tools):
    def dispatch(name, arguments):
        raise RuntimeError("private host detail")

    result = asyncio.run(
        chat_tools.execute_chat_tool(call('{"command":"ls"}', name="bash"), execute=dispatch)
    )
    assert result.is_error
    assert json.loads(result.content)["status"] == "failed"
    assert "private host detail" not in result.content


def test_generic_executor_routes_web_search_to_search_branch(chat_tools, monkeypatch):
    async def fake_search(call):
        return InteractionToolResult(call.id, '{"results":[]}')

    monkeypatch.setattr(chat_tools, "execute_web_search", fake_search)
    result = asyncio.run(chat_tools.execute_chat_tool(call('{"query":"test"}', name="web_search")))
    assert not result.is_error
    assert json.loads(result.content)["results"] == []
