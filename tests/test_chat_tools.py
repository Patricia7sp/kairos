"""Chat's only tool executes bounded searches without host-tool dispatch."""

import asyncio
import json

import httpx
import pytest

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
