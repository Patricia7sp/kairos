"""Extract source links from DuckDuckGo HTML without executing page content."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit


def _source_url(value: str) -> str | None:
    url = urljoin("https://duckduckgo.com", value)
    parsed = urlsplit(url)
    if parsed.hostname in {"duckduckgo.com", "html.duckduckgo.com"}:
        destination = parse_qs(parsed.query).get("uddg", [])
        if not destination:
            return None
        url = destination[0]
        parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return url


class _SearchResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self.empty = False
        self.challenge = False
        self._capture = ""
        self._tag = ""
        self._depth = 0
        self._text: list[str] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        identifier = attributes.get("id") or ""
        if "challenge" in identifier or any("anomaly" in item for item in classes):
            self.challenge = True
        if "no-results" in classes or "result--no-result" in classes:
            self.empty = True
        if self._capture:
            if tag == self._tag:
                self._depth += 1
            return
        if tag == "a" and "result__a" in classes:
            url = _source_url(attributes.get("href") or "")
            self._current = {"title": "", "url": url, "snippet": ""} if url else None
            self._capture = "title"
        elif "result__snippet" in classes and self._current is not None:
            self._capture = "snippet"
        else:
            return
        self._tag, self._depth, self._text = tag, 1, []

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self._capture or tag != self._tag:
            return
        self._depth -= 1
        if self._depth:
            return
        if self._current is not None:
            self._current[self._capture] = " ".join("".join(self._text).split())
            if self._capture == "title" and self._current["title"]:
                self.results.append(self._current)
        self._capture = ""


def parse_search_results(html: str, max_results: int) -> list[dict[str, str]] | None:
    """None means an unexpected/challenge page; [] means a known empty search."""
    parser = _SearchResults()
    parser.feed(html)
    parser.close()
    if parser.challenge or (not parser.results and not parser.empty):
        return None
    unique: dict[str, dict[str, str]] = {}
    for result in parser.results:
        unique.setdefault(result["url"], result)
    return list(unique.values())[:max_results]
