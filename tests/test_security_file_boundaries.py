"""Conteúdo recebido não escolhe destinos de escrita nem locks de agendamento."""

import json
from datetime import UTC, datetime

import pytest

from kairos_cron.jobs import JobStore
from kairos_tools import workspace as fs
from kairos_tools.builtin import register_builtin_tools
from kairos_tools.registry import ToolRegistry


@pytest.mark.parametrize("restricted", [False, True])
def test_conteudo_com_caminhos_nao_redireciona_edicao(tmp_path, monkeypatch, restricted):
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    root = tmp_path / "projeto"
    root.mkdir()
    target = root / "texto.txt"
    target.write_text("conteudo anterior")
    outside = tmp_path / "externo.txt"
    outside.write_text("sentinela")
    content = f"../externo.txt\n{outside}\n../../config.yaml"
    with fs.workspace_scope(root if restricted else None):
        result = registry.dispatch(
            "edit_file",
            {"path": str(target), "old_str": "conteudo anterior", "new_str": content},
        )
    assert result["success"] is True
    assert target.read_text() == content
    assert outside.read_text() == "sentinela"


@pytest.mark.parametrize("kind", ["absolute", "relative", "nul", "cycle"])
def test_path_no_documento_nao_redireciona_lock_do_jobstore(tmp_path, kind):
    home = tmp_path / "home"
    store = JobStore(home)
    job = store.create(
        name="Triagem",
        prompt="produza o resultado",
        schedule={"kind": "interval", "minutes": 60},
        now=datetime(2026, 10, 31, tzinfo=UTC),
    )
    outside = tmp_path / "externo.json"
    outside.write_text("sentinela")
    cycle = tmp_path / "ciclo"
    cycle.symlink_to(cycle)
    value = {
        "absolute": str(outside),
        "relative": "../../externo.json",
        "nul": "destino\0invalido",
        "cycle": str(cycle),
    }[kind]
    document_path = home / "cron/jobs.json"
    document = json.loads(document_path.read_text())
    document["jobs"][0]["path"] = value
    document_path.write_text(json.dumps(document))
    original = document_path.read_bytes()
    observed = JobStore(home).get(job["id"])
    assert observed["id"] == job["id"]
    assert observed["path"] == value
    assert document_path.read_bytes() == original
    assert outside.read_text() == "sentinela"
    assert not outside.with_suffix(".json.lock").exists()
    assert not (tmp_path / "ciclo.lock").exists()
