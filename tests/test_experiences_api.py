"""Experiences API — listagem, criação e revisão pelo painel."""

import pytest
from fastapi.testclient import TestClient

from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    return tmp_path


def _client():
    return TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})


def _criar(client, **overrides):
    body = {
        "trigger": "erro ao publicar imagem",
        "correction": "conferir o digest antes do push",
        "observation": "",
        "scope": "global",
        "confidence": 0.6,
        "confirm": False,
    }
    body.update(overrides)
    return client.post("/api/experiences", json=body)


def test_lista_vazia(home):
    resp = _client().get("/api/experiences")
    assert resp.status_code == 200
    assert resp.json() == {
        "items": [],
        "counts": {"ativa": 0, "candidata": 0, "invalida": 0, "total": 0},
    }


def test_cria_candidata_e_lista(home):
    client = _client()
    criada = _criar(client)
    assert criada.status_code == 200
    exp = criada.json()
    assert exp["status"] == "candidata"
    assert exp["trigger"] == "erro ao publicar imagem"
    assert exp["hits"] == 0

    lista = client.get("/api/experiences").json()
    assert [e["id"] for e in lista["items"]] == [exp["id"]]
    assert lista["counts"] == {"ativa": 0, "candidata": 1, "invalida": 0, "total": 1}


def test_cria_confirmada_vira_ativa(home):
    resp = _criar(_client(), confirm=True)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ativa"


def test_criacao_invalida_falha_fechado(home):
    client = _client()
    assert _criar(client, trigger="   ").status_code == 422
    assert _criar(client, correction="").status_code == 422
    assert _criar(client, extra="proibido").status_code == 422
    assert _criar(client, confidence=2.0).status_code == 422


def test_confirmar_candidata(home):
    client = _client()
    exp_id = _criar(client).json()["id"]
    confirmada = client.post(f"/api/experiences/{exp_id}/confirm")
    assert confirmada.status_code == 200
    assert confirmada.json()["status"] == "ativa"
    # Já ativa: transição de novo é conflito, não sucesso silencioso.
    assert client.post(f"/api/experiences/{exp_id}/confirm").status_code == 409


def test_confirmar_inexistente_e_404(home):
    assert _client().post("/api/experiences/nao-existe/confirm").status_code == 404


def test_invalidar(home):
    client = _client()
    exp_id = _criar(client).json()["id"]
    invalida = client.post(f"/api/experiences/{exp_id}/invalidate")
    assert invalida.status_code == 200
    assert invalida.json()["status"] == "invalida"
    assert client.post(f"/api/experiences/{exp_id}/invalidate").status_code == 409

    # Some da lista padrão, mas aparece em "todas".
    assert client.get("/api/experiences").json()["items"] == []
    todas = client.get("/api/experiences", params={"include_all": "true"}).json()
    assert [e["id"] for e in todas["items"]] == [exp_id]


def test_remover(home):
    client = _client()
    exp_id = _criar(client).json()["id"]
    assert client.delete(f"/api/experiences/{exp_id}").json() == {"removed": True, "id": exp_id}
    assert client.delete(f"/api/experiences/{exp_id}").status_code == 404


def test_registrar_resultado_ajusta_confianca(home):
    client = _client()
    exp_id = _criar(client, confirm=True, confidence=0.6).json()["id"]
    sucesso = client.post(f"/api/experiences/{exp_id}/record", json={"success": True})
    assert sucesso.status_code == 200
    assert sucesso.json()["hits"] == 1
    assert sucesso.json()["successes"] == 1
    assert sucesso.json()["confidence"] > 0.6

    falha = client.post(f"/api/experiences/{exp_id}/record", json={"success": False})
    assert falha.json()["hits"] == 2
    assert falha.json()["successes"] == 1
    assert falha.json()["confidence"] < sucesso.json()["confidence"]

    assert (
        client.post("/api/experiences/nao-existe/record", json={"success": True}).status_code == 404
    )


def test_filtro_status(home):
    client = _client()
    _criar(client, trigger="a")
    _criar(client, trigger="b", confirm=True)
    invalida = _criar(client, trigger="c").json()["id"]
    client.post(f"/api/experiences/{invalida}/invalidate")

    assert len(client.get("/api/experiences").json()["items"]) == 2
    assert len(client.get("/api/experiences", params={"status": "ativa"}).json()["items"]) == 1
    assert len(client.get("/api/experiences", params={"status": "candidata"}).json()["items"]) == 1
    assert len(client.get("/api/experiences", params={"status": "invalida"}).json()["items"]) == 1
    assert (
        len(client.get("/api/experiences", params={"status": "ativa,candidata"}).json()["items"])
        == 2
    )
    assert client.get("/api/experiences", params={"status": "inexistente"}).status_code == 422


def test_autenticacao_obrigatoria(home):
    assert TestClient(app).get("/api/experiences").status_code == 401
    assert (
        TestClient(app)
        .post("/api/experiences", json={"trigger": "x", "correction": "y"})
        .status_code
        == 401
    )
