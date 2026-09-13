"""Notepad durável por job: KV persistente injetado no prompt dos agendamentos.

A memória vive no ``state.db`` (migração v4, tabela ``cron_notepad``) e segue o
contrato do legado: escrita apenas via CLI, limites de tamanho documentados
(16 KiB por chave, 64 KiB por job) e prompt byte-idêntico quando o job nunca
usa o bloco.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from kairos_cli.main import main
from kairos_cron.jobs import JobStore
from kairos_cron.notepad import (
    MAX_JOB_TOTAL_BYTES,
    MAX_KEY_CHARS,
    MAX_VALUE_BYTES,
    NotepadStore,
    render_notepad_section,
)
from kairos_cron.scheduler import Scheduler
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

NOW = datetime(2026, 9, 12, 12, tzinfo=UTC)
HEADER = "## Job notepad (persistent across runs)\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    return tmp_path


def make_due(store, job_id, now):
    doc = json.loads(store.path.read_text())
    for job in doc["jobs"]:
        if job["id"] == job_id:
            job["next_run_at"] = now.isoformat()
    store.path.write_text(json.dumps(doc))


def create_job(store, now=NOW):
    return store.create(
        name="Alvo",
        prompt="Aja a partir da sua memória",
        schedule={"kind": "interval", "minutes": 5},
        now=now,
    )


# --- store core ------------------------------------------------------------


def test_set_get_list_delete_persistem(tmp_path):
    store = NotepadStore(tmp_path)
    job_id = "job-1"
    store.set(job_id, "cursor", "42", now=NOW.isoformat())
    store.set(job_id, "watch", "oss-7", now=NOW.isoformat())
    assert store.get(job_id, "cursor") == "42"
    assert store.get(job_id, "ausente") is None
    assert [n["key"] for n in store.list(job_id)] == ["cursor", "watch"]
    assert store.delete(job_id, "cursor") is True
    assert store.delete(job_id, "cursor") is False
    assert store.get(job_id, "cursor") is None


def test_upsert_substitui_valor_sem_duplicar(tmp_path):
    store = NotepadStore(tmp_path)
    job_id = "job-1"
    store.set(job_id, "cursor", "1")
    store.set(job_id, "cursor", "2")
    assert store.get(job_id, "cursor") == "2"
    assert len(store.list(job_id)) == 1


def test_keys_de_jobs_diferentes_sao_isoladas(tmp_path):
    store = NotepadStore(tmp_path)
    store.set("job-a", "cursor", "10")
    store.set("job-b", "cursor", "20")
    assert store.get("job-a", "cursor") == "10"
    assert store.get("job-b", "cursor") == "20"
    assert store.clear("job-a") == 1
    assert store.get("job-a", "cursor") is None
    assert store.get("job-b", "cursor") == "20"


def test_limites_rejeitam_sem_escrita_parcial(tmp_path):
    store = NotepadStore(tmp_path)
    with pytest.raises(ValueError):
        store.set("j", "k" * (MAX_KEY_CHARS + 1), "x")
    with pytest.raises(ValueError):
        store.set("j", "k", "1" * (MAX_VALUE_BYTES + 1))
    assert store.list("j") == []

    large = "1" * 16000
    for i in range(4):
        store.set("j", f"k{i}", large)
    assert sum(2 + 16000 for _ in range(4)) < MAX_JOB_TOTAL_BYTES
    with pytest.raises(ValueError):
        store.set("j", "z", large)
    assert [n["key"] for n in store.list("j")] == ["k0", "k1", "k2", "k3"]


# --- render e injeção ------------------------------------------------------


def test_render_vazio_devolve_string_vazia(tmp_path):
    assert render_notepad_section(tmp_path, "job-que-nao-existe") == ""


def test_render_exige_cabecalho_literal_e_instrucao_cli(tmp_path):
    store = NotepadStore(tmp_path)
    job_id = "job-1"
    store.set(job_id, "cursor", "42")
    section = render_notepad_section(tmp_path, job_id)
    assert section.startswith(HEADER)
    assert "## Job notepad (persistent across runs)" in section
    assert f"kairos cron notepad {job_id} set <key> <value>" in section
    assert "- cursor: 42" in section


def test_scheduler_injeta_somente_quando_ha_notas(tmp_path):
    store = JobStore(tmp_path)
    job = create_job(store)

    class Service:
        def __init__(self):
            self.envelopes = []

        async def stream(self, envelope):
            self.envelopes.append(envelope)
            yield SimpleNamespace(kind="turn_end")

    service = Service()
    make_due(store, job["id"], NOW)
    asyncio.run(Scheduler(tmp_path, service).tick(now=NOW))
    assert service.envelopes[0].content == job["prompt"]

    NotepadStore(tmp_path).set(job["id"], "cursor", "42")
    service = Service()
    make_due(store, job["id"], NOW + timedelta(minutes=10))
    asyncio.run(Scheduler(tmp_path, service).tick(now=NOW + timedelta(minutes=10)))
    content = service.envelopes[0].content
    assert content.startswith(HEADER)
    assert content.endswith(job["prompt"])
    assert "- cursor: 42" in content


# --- remoção do job --------------------------------------------------------


def test_remover_job_limpa_o_notepad(tmp_path):
    store = JobStore(tmp_path)
    job = create_job(store)
    NotepadStore(tmp_path).set(job["id"], "cursor", "42")
    store.remove(job["id"])
    assert NotepadStore(tmp_path).list(job["id"]) == []


def test_remover_job_sem_state_db_nao_materializa_banco(tmp_path):
    store = JobStore(tmp_path)
    job = create_job(store)
    store.remove(job["id"])
    assert not (tmp_path / "state.db").exists()


# --- CLI -------------------------------------------------------------------


def test_cli_notepad_set_get_list_delete(home, capsys):
    assert main(["cron", "create", "--name", "N", "--prompt", "x", "--every", "5", "--json"]) == 0
    job_id = json.loads(capsys.readouterr().out)["id"]
    assert main(["cron", "notepad", job_id, "set", "cursor", "42", "--json"]) == 0
    salvo = json.loads(capsys.readouterr().out)
    assert salvo == {
        "job_id": job_id,
        "key": "cursor",
        "value": "42",
        "updated_at": salvo["updated_at"],
    }
    assert main(["cron", "notepad", job_id, "get", "cursor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["value"] == "42"
    assert main(["cron", "notepad", job_id, "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["notes"][0]["key"] == "cursor"
    assert main(["cron", "notepad", job_id, "delete", "cursor", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["deleted"] is True
    assert main(["cron", "notepad", job_id, "list", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["notes"] == []


def test_cli_notepad_valida_argumentos(home, capsys):
    assert main(["cron", "notepad", "j", "set", "chave"]) != 0
    capsys.readouterr()
    assert main(["cron", "notepad", "j", "get"]) != 0
    capsys.readouterr()
    assert main(["cron", "notepad", "j", "delete"]) != 0


# --- API web ---------------------------------------------------------------


def test_api_notepad_round_trip(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    job = client.post(
        "/api/cron/jobs",
        json={"name": "N", "prompt": "x", "schedule": {"kind": "interval", "minutes": 5}},
    ).json()
    url = f"/api/cron/jobs/{job['id']}"
    assert client.get(url + "/notepad").json() == {"job_id": job["id"], "notes": []}
    assert client.put(url + "/notepad/cursor", json={"value": "42"}).status_code == 200
    assert client.get(url + "/notepad/cursor").json()["value"] == "42"
    assert len(client.get(url + "/notepad").json()["notes"]) == 1
    assert client.delete(url + "/notepad/cursor").json()["deleted"] is True
    assert client.delete(url + "/notepad").json() == {"job_id": job["id"], "cleared": 0}


def test_api_notepad_limites_e_autenticacao(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    job = client.post(
        "/api/cron/jobs",
        json={"name": "N", "prompt": "x", "schedule": {"kind": "interval", "minutes": 5}},
    ).json()
    url = f"/api/cron/jobs/{job['id']}"
    pesado = "1" * (MAX_VALUE_BYTES + 1)
    assert client.put(url + "/notepad/k", json={"value": pesado}).status_code == 422
    assert TestClient(app).put(url + "/notepad/k", json={"value": "x"}).status_code == 401
