"""Search returns source results or explicit upstream unavailability."""

import asyncio

import httpx
import pytest

from kairos_tools import builtin

RESULTS = """<html><body>
<div class="result"><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fguide%3Fa%3D1%26b%3D2&amp;rut=x">Guide <b>&amp; reference</b></a>
<a class="result__snippet">First <b>useful</b> description.</a></div>
<div class="result"><a class="result__a" href="https://example.net/second">Second source</a>
<a class="result__snippet">Another description.</a></div>
</body></html>"""


def upstream(monkeypatch, body, *, status=200):
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, text=body, headers={"Content-Type": "text/html"})

    monkeypatch.setattr(
        builtin.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    return requests


def test_search_returns_decoded_sources_and_obeys_max_results(monkeypatch):
    requests = upstream(monkeypatch, RESULTS)
    result = asyncio.run(builtin.web_search_tool("Kairos & tools", max_results=1))

    assert result["status"] == "ok"
    assert result["results"] == [
        {
            "title": "Guide & reference",
            "url": "https://example.org/guide?a=1&b=2",
            "snippet": "First useful description.",
        }
    ]
    assert requests[0].url.params["q"] == "Kairos & tools"


@pytest.mark.parametrize(
    "body",
    [
        '<html><form id="challenge-form">Please verify you are human</form></html>',
        "<html><h1>Temporarily unavailable</h1></html>",
        '<html><a class="result__a" href="javascript:alert(1)">Unsafe</a></html>',
    ],
)
def test_unexpected_or_challenge_html_is_not_a_success(monkeypatch, body):
    upstream(monkeypatch, body)
    result = asyncio.run(builtin.web_search_tool("test"))
    assert result.get("status") == "unavailable"
    assert result["error"]
    assert result.get("results", []) == []


def test_recognized_empty_search_is_distinct_from_an_upstream_failure(monkeypatch):
    upstream(monkeypatch, '<html><div class="no-results">No results found</div></html>')
    result = asyncio.run(builtin.web_search_tool("not found"))
    assert result["status"] == "ok"
    assert result["results"] == []


@pytest.mark.parametrize("limit", [0, -1, 21, True, "5"])
def test_invalid_limits_do_not_send_upstream_requests(monkeypatch, limit):
    requests = upstream(monkeypatch, RESULTS)
    result = asyncio.run(builtin.web_search_tool("test", max_results=limit))
    assert result.get("error")
    assert requests == []


def test_excessively_large_search_response_is_unavailable(monkeypatch):
    upstream(monkeypatch, RESULTS + " " * 1_100_000)
    result = asyncio.run(builtin.web_search_tool("test"))
    assert result.get("status") == "unavailable"
    assert result.get("error")


def test_http_failure_is_unavailable(monkeypatch):
    upstream(monkeypatch, "too many requests", status=429)
    result = asyncio.run(builtin.web_search_tool("test"))
    assert result.get("status") == "unavailable"
    assert result.get("error")
