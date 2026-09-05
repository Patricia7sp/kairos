from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from runtime_support import capabilities, open_runtime_db, runtime_session

from kairos_runtime import RuntimeErrorInfo, RuntimeSession, RuntimeStore
from kairos_state import SCHEMA_VERSION, connect, migrate, read_schema_version
from kairos_state.migrations import CANONICAL_TABLES
from kairos_state.repositories.runtime import RuntimeRepository


def _create_bound(repo: RuntimeRepository, project: Path, session_id: str = "runtime-1") -> str:
    session = runtime_session(project, session_id)
    repo.create_session(session, "web", allowed_directories=(str(project),))
    repo.bind_thread(session_id, f"thread-{session_id}", capabilities("text", "unknown"))
    return session_id


def test_migrate_v1_preserves_transcript_and_is_idempotent(tmp_path: Path) -> None:
    db = connect(tmp_path / "state.db")
    assert migrate(db, target=1) == 1
    db.execute("INSERT INTO sessions(id,source,started_at) VALUES ('s1','cli',1)")
    db.execute(
        "INSERT INTO messages(session_id,role,content,timestamp) VALUES ('s1','user','olá',2)"
    )
    db.commit()

    assert migrate(db) == SCHEMA_VERSION
    assert migrate(db) == SCHEMA_VERSION
    row = db.execute("SELECT session_id,role,content,timestamp FROM messages").fetchone()
    assert tuple(row) == ("s1", "user", "olá", 2.0)
    assert db.execute("SELECT execution_kind FROM sessions").fetchone()[0] == "model"


def test_v2_migration_rolls_back_all_ddl_and_version_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = connect(tmp_path / "state.db")
    migrate(db, target=1)
    before = db.execute(
        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
    ).fetchall()

    from kairos_state import migrations

    original = migrations.MIGRATIONS[1].apply

    def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
        connection.execute(
            "ALTER TABLE sessions ADD COLUMN execution_kind TEXT NOT NULL DEFAULT 'model'"
        )
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(migrations.MIGRATIONS[1], "apply", fail_after_first_ddl)
    with pytest.raises(RuntimeError, match="injected"):
        migrate(db)
    monkeypatch.setattr(migrations.MIGRATIONS[1], "apply", original)

    after = db.execute("SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    assert read_schema_version(db) == 1


def test_concurrent_migrations_converge_without_duplicate_alter(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    first = connect(path)
    migrate(first, target=1)
    first.close()
    errors: list[BaseException] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        connection = connect(path, timeout=5)
        try:
            barrier.wait()
            migrate(connection)
        except BaseException as exc:  # noqa: BLE001 - captures any startup failure
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    check = connect(path)
    assert read_schema_version(check) == SCHEMA_VERSION
    assert sum(row["name"] == "execution_kind" for row in check.execute("PRAGMA table_info(sessions)")) == 1


def test_runtime_tables_are_canonical_and_have_required_constraints(tmp_path: Path) -> None:
    db = open_runtime_db(tmp_path / "state.db")
    expected = {
        "runtime_sessions",
        "runtime_turns",
        "runtime_events",
        "runtime_approvals",
        "runtime_directory_leases",
        "runtime_queue",
    }
    assert expected <= CANONICAL_TABLES
    assert not expected & __import__("kairos_state.migrations", fromlist=["DERIVED_OBJECTS"]).DERIVED_OBJECTS
    tables = {
        row["name"]
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert expected <= tables
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runtime_turns(id,session_id,idempotency_key,content_hash,state,send_state,created_at,updated_at) "
            "VALUES ('orphan','missing','key','hash','queued','not_sent',1,1)"
        )
    db.execute("INSERT INTO sessions(id,source,started_at) VALUES ('raw','test',1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runtime_sessions("
            "session_id,runtime_kind,requested_cwd,canonical_cwd,sandbox_profile,"
            "protocol_version,state,directory_device,directory_inode,created_at,updated_at"
            ") VALUES ('raw','codex','/tmp','/tmp','read_only',2,'active',1,1,1,1)"
        )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runtime_sessions("
            "session_id,runtime_kind,requested_cwd,canonical_cwd,sandbox_profile,"
            "state,directory_device,directory_inode,created_at,updated_at"
            ") VALUES ('raw','codex','/tmp','/tmp','read_only','starting',1,1,1,1)"
        )


def test_create_authorizes_and_persists_original_directory_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    session = RuntimeSession("s1", "codex", str(alias), "workspace_write")

    repo.create_session(session, "web", allowed_directories=(str(project),))

    row = db.execute("SELECT * FROM runtime_sessions WHERE session_id='s1'").fetchone()
    stat = project.stat()
    assert row["requested_cwd"] == str(alias)
    assert row["canonical_cwd"] == str(project)
    assert (row["directory_device"], row["directory_inode"]) == (stat.st_dev, stat.st_ino)
    assert row["broad_consent_at"] is None
    assert row["state"] == "ready"
    assert db.execute("SELECT execution_kind FROM sessions WHERE id='s1'").fetchone()[0] == "agent_runtime"


def test_create_rejects_unauthorized_directory_and_broad_access_without_consent(
    tmp_path: Path,
) -> None:
    allowed = tmp_path / "allowed"
    other = tmp_path / "other"
    allowed.mkdir()
    other.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    with pytest.raises(RuntimeErrorInfo, match="invalid_directory"):
        repo.create_session(
            RuntimeSession("s1", "codex", str(other), "workspace_write"),
            "web",
            allowed_directories=(str(allowed),),
        )
    with pytest.raises(RuntimeErrorInfo, match="invalid_policy"):
        repo.create_session(
            RuntimeSession("s2", "codex", str(allowed), "broad_access"),
            "web",
            allowed_directories=(str(allowed),),
            broad_enabled=True,
            consent=False,
        )


def test_bind_negotiates_capabilities_and_freezes_identity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    repo.create_session(runtime_session(project, "s1"), "web", allowed_directories=(str(project),))
    repo.bind_thread("s1", "thread-1", capabilities("text", "unknown"))

    row = db.execute("SELECT * FROM runtime_sessions WHERE session_id='s1'").fetchone()
    assert row["protocol_version"] == 1
    assert json.loads(row["capabilities_json"]) == {"features": ["text"], "protocol_version": 1}
    assert row["state"] == "ready"
    assert repo.get_session("s1") == RuntimeSession(
        "s1", "codex", str(project), "workspace_write", "thread-1"
    )
    for sql in (
        "UPDATE sessions SET execution_kind='model' WHERE id='s1'",
        "UPDATE runtime_sessions SET runtime_kind='other' WHERE session_id='s1'",
        "UPDATE runtime_sessions SET canonical_cwd='/tmp' WHERE session_id='s1'",
        "UPDATE runtime_sessions SET sandbox_profile='read_only' WHERE session_id='s1'",
        "UPDATE runtime_sessions SET external_thread_id='thread-2' WHERE session_id='s1'",
        "UPDATE runtime_sessions SET directory_device=directory_device+1 WHERE session_id='s1'",
        "UPDATE runtime_sessions SET directory_inode=directory_inode+1 WHERE session_id='s1'",
        "UPDATE runtime_sessions SET broad_consent_at=1 WHERE session_id='s1'",
        "DELETE FROM runtime_sessions WHERE session_id='s1'",
    ):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(sql)


def test_bound_identity_cannot_be_replaced_via_insert_conflict(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    session_id = _create_bound(repo, project, "s1")
    repo.admit(session_id, "key", "content")

    with pytest.raises(sqlite3.IntegrityError, match="identity"):
        db.execute(
            "INSERT OR REPLACE INTO runtime_sessions("
            "session_id,runtime_kind,external_thread_id,requested_cwd,canonical_cwd,"
            "sandbox_profile,protocol_version,capabilities_json,state,next_sequence,"
            "directory_device,directory_inode,created_at,updated_at"
            ") SELECT session_id,runtime_kind,external_thread_id,requested_cwd,'/tmp',"
            "sandbox_profile,protocol_version,capabilities_json,state,next_sequence,"
            "directory_device,directory_inode,created_at,updated_at "
            "FROM runtime_sessions WHERE session_id='s1'"
        )
    with pytest.raises(sqlite3.IntegrityError, match="execution kind"):
        db.execute(
            "INSERT OR REPLACE INTO sessions(id,source,started_at,execution_kind) "
            "VALUES ('s1','web',1,'model')"
        )

    row = db.execute(
        "SELECT canonical_cwd FROM runtime_sessions WHERE session_id='s1'"
    ).fetchone()
    assert row["canonical_cwd"] == str(project)
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_admit_is_atomic_and_idempotent(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    session_id = _create_bound(repo, project)

    first = repo.admit(session_id, "request-1", "olá")
    assert repo.admit(session_id, "request-1", "olá") == first
    with pytest.raises(RuntimeErrorInfo, match="idempotency"):
        repo.admit(session_id, "request-1", "outro conteúdo")
    assert db.execute("SELECT count(*) FROM runtime_turns").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 1
    assert tuple(db.execute("SELECT role,content FROM messages").fetchone()) == ("user", "olá")


def test_admit_rolls_back_user_message_if_turn_insert_fails(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    session_id = _create_bound(repo, project)
    db.execute(
        "CREATE TRIGGER fail_turn BEFORE INSERT ON runtime_turns BEGIN SELECT RAISE(ABORT,'boom'); END"
    )
    with pytest.raises(sqlite3.IntegrityError, match="boom"):
        repo.admit(session_id, "key", "content")
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0


def test_admit_rejects_ended_or_unavailable_session(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    session_id = _create_bound(repo, project)
    db.execute("UPDATE sessions SET ended_at=1 WHERE id=?", (session_id,))
    db.commit()
    with pytest.raises(RuntimeErrorInfo, match="unavailable"):
        repo.admit(session_id, "ended", "content")
    db.execute("UPDATE sessions SET ended_at=NULL WHERE id=?", (session_id,))
    db.execute("UPDATE runtime_sessions SET state='recovering' WHERE session_id=?", (session_id,))
    db.commit()
    with pytest.raises(RuntimeErrorInfo, match="session_busy"):
        repo.admit(session_id, "recovering", "content")
    db.execute("UPDATE runtime_sessions SET state='unavailable' WHERE session_id=?", (session_id,))
    db.commit()
    with pytest.raises(RuntimeErrorInfo, match="unavailable"):
        repo.admit(session_id, "unavailable", "content")


def test_append_sequences_events_and_returns_snapshot_isolated_payload(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    turn = repo.admit(_create_bound(repo, project), "key", "content")
    payload = {"items": [{"text": "one"}]}

    first = repo.append(turn, "event-1", "output", payload)
    payload["items"][0]["text"] = "mutated"
    second = repo.append(turn, "event-2", "output", {"text": "two"})

    assert (first.sequence, first.cursor, first.payload["items"][0]["text"]) == (
        1,
        "v1:runtime-1:1",
        "one",
    )
    assert (second.sequence, second.cursor) == (2, "v1:runtime-1:2")
    assert [event.event_id for event in repo.events_after("runtime-1", first.cursor)] == [
        "event-2"
    ]


def test_append_returns_the_same_payload_snapshot_persisted_before_lock_wait(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    setup = open_runtime_db(path)
    turn = RuntimeRepository(setup).admit(
        _create_bound(RuntimeRepository(setup), project), "key", "content"
    )
    setup.close()

    begin_attempted = threading.Event()
    payload = {"text": "before"}
    locker = connect(path, timeout=5)
    locker.execute("BEGIN IMMEDIATE")
    result: list[object] = []

    def append_while_locked() -> None:
        connection = connect(path, timeout=5)
        connection.set_trace_callback(
            lambda statement: begin_attempted.set()
            if statement == "BEGIN IMMEDIATE"
            else None
        )
        try:
            result.append(RuntimeRepository(connection).append(turn, "event", "output", payload))
        except BaseException as exc:  # noqa: BLE001 - thread must return all failures
            result.append(exc)
        finally:
            connection.close()

    worker = threading.Thread(target=append_while_locked)
    worker.start()
    assert begin_attempted.wait(timeout=2)
    payload["text"] = "after"
    locker.commit()
    worker.join(timeout=5)
    locker.close()

    assert not worker.is_alive()
    assert len(result) == 1
    assert not isinstance(result[0], BaseException)
    event = result[0]
    assert event.payload["text"] == "before"
    check = connect(path)
    assert json.loads(check.execute("SELECT payload_json FROM runtime_events").fetchone()[0]) == {
        "text": "before"
    }
    check.close()


def test_append_deduplicates_identical_event_and_rejects_conflicts(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    turn = repo.admit(_create_bound(repo, project), "key", "content")
    event = repo.append(turn, "event-1", "output", {"text": "one"})
    assert repo.append(turn, "event-1", "output", {"text": "one"}) == event
    with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
        repo.append(turn, "event-1", "output", {"text": "different"})
    with pytest.raises(RuntimeErrorInfo, match="invalid_event"):
        repo.append(turn, "event-1", "other", {"text": "one"})
    assert db.execute("SELECT next_sequence FROM runtime_sessions").fetchone()[0] == 2


def test_events_after_rejects_malformed_foreign_and_future_cursors(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    first_turn = repo.admit(_create_bound(repo, project, "s1"), "key", "one")
    second_turn = repo.admit(_create_bound(repo, project, "s2"), "key", "two")
    first = repo.append(first_turn, "e1", "output", {})
    foreign = repo.append(second_turn, "e2", "output", {})
    assert repo.events_after("s1", None) == (first,)
    for cursor in ("garbage", foreign.cursor, "v1:s1:999"):
        with pytest.raises(RuntimeErrorInfo):
            repo.events_after("s1", cursor)


def test_composite_event_fk_rejects_wrong_session_turn_pair(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    db = open_runtime_db(tmp_path / "state.db")
    repo = RuntimeRepository(db)
    turn = repo.admit(_create_bound(repo, project, "s1"), "key", "one")
    _create_bound(repo, project, "s2")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO runtime_events(event_id,session_id,turn_id,sequence,kind,payload_json,cursor,created_at,updated_at) "
            "VALUES ('bad','s2',?,1,'output','{}','v1:s2:1',1,1)",
            (turn,),
        )


def test_async_store_opens_connections_in_worker_and_propagates_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    db = open_runtime_db(path)
    db.close()
    store = RuntimeStore(path)

    async def exercise() -> None:
        session = runtime_session(project)
        await store.create_session(session, "web", allowed_directories=(str(project),))
        await store.bind_thread("runtime-1", "thread-1", capabilities("text"))
        turn = await store.admit("runtime-1", "key", "content")
        event = await store.append(turn, "event", "output", {"text": "ok"})
        assert await store.get_session("runtime-1") == RuntimeSession(
            "runtime-1", "codex", str(project), "workspace_write", "thread-1"
        )
        assert await store.events_after("runtime-1", None) == (event,)

        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        original = RuntimeRepository.get_session

        def blocked_get(repo: RuntimeRepository, session_id: str):
            entered.set()
            try:
                release.wait(timeout=2)
                return original(repo, session_id)
            finally:
                finished.set()

        monkeypatch.setattr(RuntimeRepository, "get_session", blocked_get)
        task = asyncio.create_task(store.get_session("runtime-1"))
        while not entered.is_set():
            await asyncio.sleep(0.001)
        timer = threading.Timer(0.05, release.set)
        timer.start()
        task.cancel()
        asyncio.get_running_loop().call_later(0.01, task.cancel)
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert finished.is_set(), "cancel propagated before the worker connection closed"
        finally:
            release.set()
            timer.cancel()

    asyncio.run(exercise())


def test_asyncio_run_shutdown_drains_store_worker_without_cancelling_it(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    db = open_runtime_db(path)
    repo = RuntimeRepository(db)
    _create_bound(repo, project)
    db.close()
    marker = tmp_path / "worker-finished"
    script = textwrap.dedent(
        """
        import asyncio
        import sys
        import time
        from pathlib import Path

        from kairos_runtime import RuntimeStore
        from kairos_state.repositories.runtime import RuntimeRepository

        db_path = Path(sys.argv[1])
        marker = Path(sys.argv[2])
        original = RuntimeRepository.get_session

        def slow_get(repo, session_id):
            time.sleep(0.2)
            result = original(repo, session_id)
            marker.write_text("done", encoding="utf-8")
            return result

        RuntimeRepository.get_session = slow_get

        async def main():
            asyncio.create_task(RuntimeStore(db_path).get_session("runtime-1"))
            await asyncio.sleep(0.05)

        asyncio.run(main())
        """
    )

    completed = subprocess.run(
        [sys.executable, "-c", script, str(path), str(marker)],
        cwd=Path(__file__).parents[1],
        text=True,
        capture_output=True,
        timeout=3,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert marker.read_text(encoding="utf-8") == "done"
