from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Callable
from functools import wraps
from pathlib import Path

import pytest
from runtime_support import capabilities, open_runtime_db, runtime_session

from kairos_runtime import RuntimeErrorInfo, RuntimeLeaseManager, RuntimeStore
from kairos_state import connect


class Clock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def async_test(function: Callable[..., object]) -> Callable[..., None]:
    @wraps(function)
    def run(*args: object, **kwargs: object) -> None:
        asyncio.run(function(*args, **kwargs))  # type: ignore[arg-type]

    return run


async def _admit(store: RuntimeStore, project: Path, session_id: str) -> str:
    await store.create_session(
        runtime_session(project, session_id),
        "web",
        allowed_directories=(str(project),),
    )
    await store.bind_thread(session_id, f"thread-{session_id}", capabilities("text"))
    return await store.admit(session_id, f"key-{session_id}", f"content-{session_id}")


def _set_turn_state(path: Path, turn_id: str, state: str) -> None:
    db = connect(path)
    db.execute("UPDATE runtime_turns SET state=? WHERE id=?", (state, turn_id))
    db.commit()
    db.close()


@async_test
async def test_fifo_serializes_same_directory_while_other_directory_runs(
    tmp_path: Path,
) -> None:
    project_p = tmp_path / "p"
    project_q = tmp_path / "q"
    project_p.mkdir()
    project_q.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store_a = RuntimeStore(path)
    store_b = RuntimeStore(path)
    clock = Clock()
    leases_a = RuntimeLeaseManager(store_a, clock)
    leases_b = RuntimeLeaseManager(store_b, clock)
    turn_a = await _admit(store_a, project_p, "a")
    turn_b = await _admit(store_b, project_p, "b")
    turn_c = await _admit(store_a, project_q, "c")
    for turn in (turn_a, turn_b, turn_c):
        await leases_a.enqueue(turn)

    generation_a = await leases_a.claim(turn_a, "host-1", 30)
    assert generation_a is not None
    assert await leases_b.claim(turn_b, "host-1", 30) is None
    assert await leases_b.claim(turn_c, "host-1", 30) is not None

    _set_turn_state(path, turn_a, "completed")
    assert await leases_a.release(turn_a, "host-1", generation_a)
    generation_b = await leases_b.claim(turn_b, "host-1", 30)
    assert generation_b is not None
    assert generation_b > generation_a


@async_test
async def test_fifo_rejects_claim_that_skips_older_waiting_ticket(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    first = await _admit(store, project, "first")
    second = await _admit(store, project, "second")
    await leases.enqueue(first)
    await leases.enqueue(second)

    assert await leases.claim(second, "host", 30) is None
    assert await leases.claim(first, "host", 30) == 1


@async_test
async def test_generation_fences_old_owner_after_release_and_reacquire(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    clock = Clock()
    leases = RuntimeLeaseManager(store, clock)
    first = await _admit(store, project, "first")
    second = await _admit(store, project, "second")
    await leases.enqueue(first)
    await leases.enqueue(second)
    old_generation = await leases.claim(first, "same-host", 30)
    assert old_generation == 1
    _set_turn_state(path, first, "completed")
    assert await leases.release(first, "same-host", old_generation)
    new_generation = await leases.claim(second, "same-host", 30)
    assert new_generation == 2

    assert not await leases.renew(first, "same-host", old_generation, 30)
    assert not await leases.release(first, "same-host", old_generation)


@async_test
async def test_expired_uncertain_owner_quarantines_until_external_inactivity(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    clock = Clock()
    leases = RuntimeLeaseManager(store, clock)
    first = await _admit(store, project, "first")
    second = await _admit(store, project, "second")
    await leases.enqueue(first)
    await leases.enqueue(second)
    generation = await leases.claim(first, "old-host", 30)
    assert generation == 1
    _set_turn_state(path, first, "interrupted")
    clock.now += 31

    assert await leases.claim(second, "new-host", 30) is None
    db = connect(path)
    lease = db.execute(
        "SELECT turn_id,generation,quarantined FROM runtime_directory_leases"
    ).fetchone()
    assert tuple(lease) == (first, 1, 1)
    assert (
        db.execute("SELECT state FROM runtime_sessions WHERE session_id='first'").fetchone()[0]
        == "recovering"
    )
    db.close()
    assert not await leases.renew(first, "old-host", generation, 30)
    assert await leases.claim(second, "new-host", 30) is None


@async_test
async def test_queued_cancellation_retires_turn_without_runtime_rpc(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, project, "queued")
    await leases.enqueue(turn)

    assert await leases.cancel_queued(turn)
    assert not await leases.cancel_queued(turn)
    db = connect(path)
    assert tuple(
        db.execute(
            "SELECT t.state,q.state FROM runtime_turns t JOIN runtime_queue q ON q.turn_id=t.id"
        ).fetchone()
    ) == ("cancelled", "cancelled")
    assert db.execute("SELECT count(*) FROM session_turn_leases").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM runtime_directory_leases").fetchone()[0] == 0
    db.close()


@async_test
async def test_claim_revalidates_original_symlink_identity(tmp_path: Path) -> None:
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(original, target_is_directory=True)
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, alias, "session")
    await leases.enqueue(turn)
    alias.unlink()
    alias.symlink_to(replacement, target_is_directory=True)

    with pytest.raises(RuntimeErrorInfo, match="invalid_directory"):
        await leases.claim(turn, "host", 30)
    db = connect(path)
    assert db.execute("SELECT count(*) FROM session_turn_leases").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM runtime_directory_leases").fetchone()[0] == 0
    assert db.execute("SELECT state FROM runtime_queue").fetchone()[0] == "waiting"
    db.close()


@async_test
async def test_claim_rolls_back_both_leases_and_queue_on_failure(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    db = open_runtime_db(path)
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, project, "session")
    await leases.enqueue(turn)
    db.execute(
        "CREATE TRIGGER fail_runtime_directory_lease BEFORE INSERT ON runtime_directory_leases "
        "BEGIN SELECT RAISE(ABORT,'injected lease failure'); END"
    )
    db.commit()
    db.close()

    with pytest.raises(sqlite3.IntegrityError, match="injected lease failure"):
        await leases.claim(turn, "host", 30)
    check = connect(path)
    assert check.execute("SELECT count(*) FROM session_turn_leases").fetchone()[0] == 0
    assert check.execute("SELECT count(*) FROM runtime_directory_leases").fetchone()[0] == 0
    assert check.execute("SELECT state FROM runtime_queue").fetchone()[0] == "waiting"
    assert check.execute("SELECT state FROM runtime_turns").fetchone()[0] == "queued"
    check.close()


@async_test
async def test_busy_database_is_contention_not_success(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, project, "session")
    await leases.enqueue(turn)
    locker = connect(path, timeout=0)
    locker.execute("BEGIN IMMEDIATE")
    try:
        assert await leases.claim(turn, "host", 30) is None
    finally:
        locker.rollback()
        locker.close()


@async_test
async def test_release_requires_confirmed_inactivity(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, project, "session")
    await leases.enqueue(turn)
    generation = await leases.claim(turn, "host", 30)
    assert generation == 1

    _set_turn_state(path, turn, "interrupted")
    assert not await leases.release(turn, "host", generation)
    await leases.quarantine(turn)
    assert not await leases.release(turn, "host", generation)
    _set_turn_state(path, turn, "completed")
    assert await leases.release(turn, "host", generation)
    db = connect(path)
    assert db.execute("SELECT quarantined FROM runtime_directory_leases").fetchone()[0] == 0
    db.close()


@async_test
async def test_replaced_directory_inode_is_rejected_even_at_same_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    path = tmp_path / "state.db"
    open_runtime_db(path).close()
    store = RuntimeStore(path)
    leases = RuntimeLeaseManager(store, Clock())
    turn = await _admit(store, project, "session")
    await leases.enqueue(turn)
    moved = tmp_path / "old-project"
    os.rename(project, moved)
    project.mkdir()

    with pytest.raises(RuntimeErrorInfo, match="invalid_directory"):
        await leases.claim(turn, "host", 30)
