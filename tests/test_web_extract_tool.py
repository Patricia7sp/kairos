"""web_extract: abre URL e devolve corpo legível, com limites de custo."""

import asyncio

import httpx
import pytest

from kairos_tools import web_extract as mod

PAGE = """<html><head>
<style>body{color:red}</style>
<title>Guia &amp; Manual</title></head>
<body><script>alert(1)</script>
<h1>Título</h1>
<p>Primeiro parágrafo com <b>negrito</b>.</p>
<ul><li>item</li></ul>
</body></html>"""


def upstream(monkeypatch, body, *, status=200, content_type="text/html"):
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, text=body, headers={"Content-Type": content_type})

    monkeypatch.setattr(
        mod.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)),
    )
    return requests


def _run(**kwargs):
    return asyncio.run(mod.web_extract_tool(**kwargs))


def test_extrai_texto_e_titulo_ignorando_script_e_style(monkeypatch):
    upstream(monkeypatch, PAGE)
    result = _run(url="https://exemplo.org/guia")

    assert result["status"] == "ok"
    assert result["title"] == "Guia & Manual"
    assert "alert(1)" not in result["content"]
    assert "Primeiro parágrafo com negrito." in result["content"]
    assert "Título" in result["content"]


def test_html_to_text_trata_bloco_e_paragrafo_como_quebra(tmp_path):
    pagina = "<div><p>A</p><p>B</p><span>C <b>D</b></span></div>"
    texto, titulo = mod.html_to_text(pagina)
    assert titulo is None
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    assert "A" in "\n".join(linhas)
    assert "B" in "\n".join(linhas)
    assert "C D" in "\n".join(linhas)


def test_status_nao_200_e_unavailable(monkeypatch):
    upstream(monkeypatch, "erro", status=503)
    result = _run(url="https://exemplo.org/queda")
    assert result["status"] == "unavailable"
    assert result.get("error")


def test_resposta_nao_html_e_unavailable(monkeypatch):
    upstream(monkeypatch, '{"json": true}', content_type="application/json")
    result = _run(url="https://exemplo.org/api")
    assert result["status"] == "unavailable"
    assert result.get("error")


def test_corpo_grande_demais_e_unavailable(monkeypatch):
    upstream(monkeypatch, "<p>" + "x" * 1_200_000 + "</p>")
    result = _run(url="https://exemplo.org/lista")
    assert result["status"] == "unavailable"
    assert result.get("error")


def test_limite_de_caracteres_trunca_e_sinaliza(monkeypatch):
    upstream(monkeypatch, "<p>" + "palavra " * 1000 + "</p>")
    result = _run(url="https://exemplo.org/fonte", max_chars=200)
    assert result["status"] == "ok"
    assert result["truncated"] is True
    assert len(result["content"]) <= 200


def test_pagina_sem_texto_e_unavailable(monkeypatch):
    upstream(monkeypatch, "<html><head></head><body><!-- vazio --></body></html>")
    result = _run(url="https://exemplo.org/vazio")
    assert result["status"] == "unavailable"
    assert result.get("error")


@pytest.mark.parametrize("url", ["ftp://exemplo.org/x", "not a url", ""])
def test_url_invalida_nao_envia_requisicao(monkeypatch, url):
    requests = upstream(monkeypatch, PAGE)
    result = _run(url=url)
    assert result["status"] == "invalid_arguments"
    assert result.get("error")
    assert requests == []


@pytest.mark.parametrize("chars", [50, 100_000, "5", 3.5])
def test_max_chars_invalido_e_recusado(monkeypatch, chars):
    requests = upstream(monkeypatch, PAGE)
    result = _run(url="https://exemplo.org/x", max_chars=chars)
    assert result["status"] == "invalid_arguments"
    assert requests == []
