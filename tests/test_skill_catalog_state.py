"""Catálogo durável: concorrência, corrupção e conservação do estado."""

import asyncio
import multiprocessing
import sqlite3
import threading

import pytest
from test_skill_runtime import skill_text, write_skill

from kairos_skills.catalog import SkillCatalogError, catalog_asset
from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
from kairos_state import connect, initialize_schema
from kairos_state.repositories import MessageRepository, SessionRepository


def _prepare(home):
    from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

    write_skill(home)
    connection = connect(home / "state.db")
    initialize_schema(connection)
    SessionRepository(connection).ensure("s1", source="cli")
    return connection, SkillCatalogRepository(connection), capture_skill_catalog(home)


def _race(path, snapshot, barrier, queue):
    from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

    db = connect(path)
    try:
        barrier.wait(timeout=10)
        winner = SkillCatalogRepository(db).create_if_absent("s1", snapshot)
        queue.put((winner.digest, winner.system_text))
    finally:
        db.close()


def test_worker_preserva_snapshot_e_asset_no_reinicio(tmp_path):
    from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

    db, repo, snapshot = _prepare(tmp_path)
    asset = catalog_asset(snapshot, "revisar-docs")
    original = read_catalog_asset(tmp_path, asset)
    assert repo.create_if_absent("s1", snapshot) == snapshot
    assert repo.put_asset("s1", asset, original) == original
    db.close()
    write_skill(tmp_path, text=skill_text(body="nova versão"))
    with connect(tmp_path / "state.db") as db:
        repo = SkillCatalogRepository(db)
        assert repo.get("s1", home_id=snapshot.home_id) == snapshot
        assert repo.get_asset("s1", asset) == original
        with pytest.raises(SkillCatalogError):
            repo.get("s1", home_id="0" * 64)
        assert repo.get("nova", home_id=snapshot.home_id) is None
    db.close()


@pytest.mark.parametrize(
    "target", ["manifest", "system", "digest", "asset", "asset-hash", "asset-name"]
)
def test_catalogo_ou_asset_corrompido_recusa_sem_reindexar(tmp_path, target):
    db, repo, snapshot = _prepare(tmp_path)
    asset = catalog_asset(snapshot, "revisar-docs")
    repo.create_if_absent("s1", snapshot)
    repo.put_asset("s1", asset, read_catalog_asset(tmp_path, asset))
    queries = {
        "manifest": "UPDATE skill_catalogs SET manifest_json='{}'",
        "system": "UPDATE skill_catalogs SET system_text='adulterado'",
        "digest": "UPDATE skill_catalogs SET digest='adulterado'",
        "asset": "UPDATE skill_catalog_assets SET text='adulterado'",
        "asset-hash": "UPDATE skill_catalog_assets SET sha256='adulterado'",
        "asset-name": "UPDATE skill_catalog_assets SET name='ausente'",
    }
    with db:
        db.execute(queries[target])
    db.close()
    with connect(tmp_path / "state.db") as db:
        from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

        with pytest.raises(SkillCatalogError):
            SkillCatalogRepository(db).get("s1", home_id=snapshot.home_id)
    db.close()


def test_dois_processos_usam_snapshot_vencedor(tmp_path):
    db, _repo, first = _prepare(tmp_path)
    write_skill(tmp_path, text=skill_text(body="snapshot B"))
    second = capture_skill_catalog(tmp_path)
    db.close()
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    queue = context.Queue()
    processes = [
        context.Process(target=_race, args=(tmp_path / "state.db", s, barrier, queue))
        for s in (first, second)
    ]
    try:
        for process in processes:
            process.start()
        results = [queue.get(timeout=15) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert results[0] == results[1]
        assert results[0][0] in (first.digest, second.digest)
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        queue.close()


def test_rollback_insert_e_asset_invalido_nao_deixam_parcial(tmp_path):
    db, repo, snapshot = _prepare(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        repo.create_if_absent("ausente", snapshot)
    assert repo.get("ausente", home_id=snapshot.home_id) is None
    repo.create_if_absent("s1", snapshot)
    asset = catalog_asset(snapshot, "revisar-docs")
    with pytest.raises(SkillCatalogError):
        repo.put_asset("s1", asset, "versão incorreta")
    assert repo.get_asset("s1", asset) is None
    db.close()


def test_migracao_idempotente_preserva_transcript_e_selecao(tmp_path):
    from kairos_state.migrations import MIGRATIONS, migrate
    from kairos_state.repositories.skill_catalogs import SkillCatalogRepository

    path = tmp_path / "state.db"
    db = connect(path)
    migrate(db, target=MIGRATIONS[-2].version)
    SessionRepository(db).create(
        "s1", source="cli", model="modelo-antigo", model_config='{"provider":"local"}'
    )
    message = MessageRepository(db).append(
        "s1", "user", content="texto exibido", api_content="texto enviado"
    )
    before = tuple(db.execute("SELECT model,model_config FROM sessions WHERE id='s1'").fetchone())
    migrate(db)
    migrate(db)
    assert (
        tuple(db.execute("SELECT model,model_config FROM sessions WHERE id='s1'").fetchone())
        == before
    )
    row = db.execute("SELECT content,api_content FROM messages WHERE id=?", (message,)).fetchone()
    assert tuple(row) == ("texto exibido", "texto enviado")
    write_skill(tmp_path)
    snapshot = capture_skill_catalog(tmp_path)
    assert SkillCatalogRepository(db).create_if_absent("s1", snapshot) == snapshot
    backup = sqlite3.connect(tmp_path / "backup.db")
    db.backup(backup)
    backup.close()
    db.close()
    with connect(tmp_path / "backup.db") as db:
        assert SkillCatalogRepository(db).get("s1", home_id=snapshot.home_id) == snapshot
    db.close()


def test_reparo_e_exportacao_preservam_catalogo(tmp_path):
    from kairos_state.migrations import repair_derived_objects

    db, repo, snapshot = _prepare(tmp_path)
    asset = catalog_asset(snapshot, "revisar-docs")
    text = read_catalog_asset(tmp_path, asset)
    repo.create_if_absent("s1", snapshot)
    repo.put_asset("s1", asset, text)
    messages = MessageRepository(db)
    first = messages.append("s1", "user", content="primeiro")
    messages.append("s1", "assistant", content="segundo")
    before = repo.export_session("s1")
    repair_derived_objects(db)
    messages.rewind("s1", first)
    assert repo.export_session("s1") == before
    assert before["manifest_json"] == snapshot.manifest_json
    assert before["system_text"] == snapshot.system_text
    assert before["assets"][0]["text"] == text
    assert before["assets"][0]["sha256"] == asset.sha256
    SessionRepository(db).create("nova", source="cli", parent_session_id="s1")
    assert repo.export_session("nova") is None
    db.close()


def test_reparo_reconstroi_busca_de_mensagens_existentes(tmp_path):
    from kairos_state.migrations import repair_derived_objects
    from kairos_state.repositories import SearchIndex

    db, _repo, _snapshot = _prepare(tmp_path)
    messages = MessageRepository(db)
    messages.append("s1", "user", content="documentação fictícia")
    repair_derived_objects(db)
    assert [row["content"] for row in SearchIndex(db).search("documen*")] == [
        "documentação fictícia"
    ]
    db.close()


def test_async_worker_persiste_asset_e_cancelamento_aguarda_commit(tmp_path):
    async def exercise():
        from kairos_integration.persistence import SQLiteAsyncInteractionPersistence

        db, _repo, snapshot = _prepare(tmp_path)
        db.close()
        worker = SQLiteAsyncInteractionPersistence(tmp_path / "state.db")
        entered = threading.Event()
        release = threading.Event()
        try:
            assert await worker.get_skill_catalog("s1", home_id=snapshot.home_id) is None
            assert await worker.create_skill_catalog_if_absent("s1", snapshot) == snapshot
            asset = catalog_asset(snapshot, "revisar-docs")
            text = read_catalog_asset(tmp_path, asset)

            # Operação real serializada atrás de um trabalho controlado no executor.
            def blocker():
                entered.set()
                release.wait(timeout=5)

            pending = worker._executor.submit(blocker)
            assert await asyncio.to_thread(entered.wait, 2)
            operation = asyncio.create_task(worker.put_skill_asset("s1", asset, text))
            await asyncio.sleep(0.02)
            operation.cancel()
            await asyncio.sleep(0.02)
            assert not operation.done()
            release.set()
            pending.result(timeout=2)
            with pytest.raises(asyncio.CancelledError):
                await operation
            assert await worker.get_skill_asset("s1", asset) == text
            assert (await worker.export_skill_catalog("s1"))["assets"][0]["text"] == text
        finally:
            release.set()
            await worker.aclose()
        restarted = SQLiteAsyncInteractionPersistence(tmp_path / "state.db")
        try:
            assert await restarted.get_skill_catalog("s1", home_id=snapshot.home_id) == snapshot
            assert await restarted.get_skill_asset("s1", asset) == text
        finally:
            await restarted.aclose()

    asyncio.run(exercise())
