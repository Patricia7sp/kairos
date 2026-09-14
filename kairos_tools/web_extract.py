"""Extrai texto legível de páginas web.

Complemento do `web_search`: a busca devolve títulos e trechos; esta
ferramenta abre a fonte e devolve o corpo em texto. Resposta limitada em
bytes de rede (mesma regra da busca) e em caracteres de saída. Conteúdo web
é dado não confiável: a ferramenta nunca interpreta a página como instrução,
só a devolve como texto.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import Any

import httpx

MAX_BODY_BYTES = 1_048_576  # 1 MiB
MAX_CHARS = 20_000
DEFAULT_MAX_CHARS = 5_000
TIMEOUT = 15.0

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
_BLOCK_TAGS = re.compile(
    r"<(script|style|noscript|template|svg|iframe)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")
_VOID_TAGS = {"br", "hr", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
_INLINE_TAGS = {"b", "strong", "em", "i", "u", "a", "code", "span", "mark", "kbd", "sub", "sup"}
_HEAD_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_WS_RE = re.compile(r"[ \t]+")


def _decode_entities(text: str) -> str:
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
    text = text.replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
    return text


def _html_to_text(body: str) -> tuple[str, str | None]:
    """Converte HTML em texto legível, devolvendo (texto, título|None)."""
    title = None
    match = _HEAD_RE.search(body)
    if match and match.group(1).strip():
        title = _decode_entities(_TAG_RE.sub("", match.group(1)).strip()).replace("\n", " ")
    text = _BLOCK_TAGS.sub(" ", body)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = _TAG_RE.sub(_tag_to_separator, text)
    text = _decode_entities(text)
    lines = (_WS_RE.sub(" ", line).strip() for line in text.splitlines())
    lines = [line for line in lines if line]
    return ("\n".join(lines), title)


def _tag_to_separator(match: re.Match[str]) -> str:
    raw = match.group(0)
    name = raw.lower().strip("</>").split()[0]
    if name in _VOID_TAGS:
        return "\n"
    if name in _INLINE_TAGS:
        return ""
    return " "


def _cap_text(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


async def web_extract_tool(
    url: str, max_chars: int = DEFAULT_MAX_CHARS, timeout: float = TIMEOUT
) -> dict[str, Any]:
    """Abre `url` e devolve o corpo em texto legível, com título se houver."""
    if not isinstance(url, str) or not url.strip():
        return {"status": "invalid_arguments", "error": "Informe uma URL não vazia."}
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {"status": "invalid_arguments", "error": "URL inválida (esperado http/https)."}
    if type(max_chars) is not int or not 100 <= max_chars <= MAX_CHARS:
        return {
            "status": "invalid_arguments",
            "error": f"max_chars deve ser um inteiro entre 100 e {MAX_CHARS}.",
        }
    if timeout <= 0:
        return {"status": "invalid_arguments", "error": "timeout deve ser positivo."}

    unavailable = {"url": url, "status": "unavailable", "content": ""}
    try:
        async with (
            httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client,
            client.stream("GET", url, headers={"User-Agent": _USER_AGENT}) as res,
        ):
            if res.status_code != 200:
                return {**unavailable, "error": f"Status HTTP {res.status_code} ao abrir"}
            content_type = res.headers.get("content-type", "")
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                return {**unavailable, "error": "A URL não retornou conteúdo textual (HTML)."}
            body = bytearray()
            async for chunk in res.aiter_bytes(chunk_size=16_384):
                if len(body) + len(chunk) > MAX_BODY_BYTES:
                    return {**unavailable, "error": "A resposta excedeu o limite de 1 MiB."}
                body.extend(chunk)
    except httpx.HTTPError as exc:
        return {**unavailable, "error": f"Falha ao abrir a URL: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {**unavailable, "error": f"Falha ao abrir a URL: {exc}"}

    text, title = _html_to_text(body.decode("utf-8", errors="replace"))
    if not text.strip():
        return {**unavailable, "error": "A página não continha texto extraível."}
    capped, truncated = _cap_text(text, max_chars)
    payload: dict[str, Any] = {
        "url": url,
        "status": "ok",
        "content": capped,
        "truncated": truncated,
    }
    if title:
        payload["title"] = title
    return payload


__all__ = [
    "DEFAULT_MAX_CHARS",
    "MAX_BODY_BYTES",
    "MAX_CHARS",
    "TIMEOUT",
    "html_to_text",
    "web_extract_tool",
]

html_to_text = _html_to_text  # exposto para os testes usarem sem download
