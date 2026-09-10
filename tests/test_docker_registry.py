"""Broker-owned checkpoint boundary, using no Docker or credentials."""

import io
import os
import sqlite3
import tarfile
from dataclasses import replace

import pytest

from kairos_runtime.contracts import RuntimeSession
from kairos_runtime.docker_backend.archives import validate_archive
from kairos_runtime.docker_backend.registry import SessionRegistry
from kairos_runtime.experimental.snapshot import snapshot_project


def archive(*entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for name, kind, data in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if kind == tarfile.REGTYPE:
                member.size = len(data)
            elif kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = data.decode()
            tar.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
    return output.getvalue()


@pytest.fixture
def session(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    return RuntimeSession("session-1", "codex", str(project), "workspace_write")


@pytest.fixture
def registry(tmp_path):
    result = SessionRegistry(tmp_path / "state")
    yield result
    result.close()


@pytest.mark.parametrize(
    "name", ["../escape", "/absolute", "a/../b", "a//b", "./a", "a\\b", "a\x00b", "x" * 4097]
)
def test_archive_rejects_noncanonical_paths(name):
    with pytest.raises(ValueError):
        validate_archive(archive((name, tarfile.REGTYPE, b"bad")))


@pytest.mark.parametrize(
    "kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE]
)
def test_archive_rejects_links_and_special_files(kind):
    with pytest.raises(ValueError):
        validate_archive(archive(("bad", kind, b"/etc/passwd")))


@pytest.mark.parametrize(
    "entries",
    [
        [("a", tarfile.REGTYPE, b"1"), ("a", tarfile.REGTYPE, b"2")],
        [("a", tarfile.REGTYPE, b"1"), ("a/b", tarfile.REGTYPE, b"2")],
        [("a/b", tarfile.REGTYPE, b"1"), ("a", tarfile.REGTYPE, b"2")],
        [("a", tarfile.DIRTYPE, b""), ("a", tarfile.REGTYPE, b"2")],
    ],
)
def test_archive_rejects_duplicates_and_ancestor_conflicts(entries):
    with pytest.raises(ValueError):
        validate_archive(archive(*entries))


def test_archive_validates_snapshot_without_extracting(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "nested").mkdir()
    (project / "nested" / "file").write_text("hello")
    (project / ("long-" + "x" * 150)).write_text("long filename")
    blob = snapshot_project(project)
    assert validate_archive(blob) == blob
    assert validate_archive(archive()) == archive()


@pytest.mark.parametrize(
    "blob",
    [b"", b"invalid tar", archive(("x", tarfile.REGTYPE, b"hi"))[:513], archive() + b"hidden"],
)
def test_archive_rejects_corrupt_truncated_and_trailing_content(blob):
    with pytest.raises(ValueError):
        validate_archive(blob)


def test_archive_bounds_bytes_content_and_entries(monkeypatch):
    import kairos_runtime.docker_backend.archives as boundary

    monkeypatch.setattr(boundary, "MAX_ARCHIVE_BYTES", 10239)
    with pytest.raises(ValueError):
        validate_archive(archive())
    monkeypatch.setattr(boundary, "MAX_ARCHIVE_BYTES", 80 * 1024**2)
    monkeypatch.setattr(boundary, "MAX_CONTENT_BYTES", 2)
    with pytest.raises(ValueError):
        validate_archive(archive(("x", tarfile.REGTYPE, b"123")))
    monkeypatch.setattr(boundary, "MAX_ENTRIES", 1)
    with pytest.raises(ValueError):
        validate_archive(archive(("x", tarfile.REGTYPE, b""), ("y", tarfile.DIRTYPE, b"")))


def test_registry_survives_restart_with_worker_and_atomic_pair(tmp_path, session):
    root = tmp_path / "state"
    registry = SessionRegistry(root)
    record = registry.create(session)
    assert (record.directory_device, record.directory_inode) == (
        os.stat(session.cwd).st_dev,
        os.stat(session.cwd).st_ino,
    )
    registry.record_worker(session.session_id, "kairos-worker-1")
    assert registry.get(session.session_id).worker_confirmed is False
    registry.close()
    registry = SessionRegistry(root)
    assert registry.list_workers()[0].worker_confirmed is False
    registry.worker_created(session.session_id)
    workspace, home = archive(("edited", tarfile.REGTYPE, b"new")), archive()
    registry.checkpoint(session.session_id, workspace, home, thread_id="thread-1")
    registry.close()
    reopened = SessionRegistry(root)
    assert reopened.archives(session.session_id) == (workspace, home)
    assert reopened.get(session.session_id).external_thread_id == "thread-1"
    assert reopened.list_workers()[0].worker_name == "kairos-worker-1"
    assert reopened.list_workers()[0].worker_confirmed is True
    reopened.worker_removed(session.session_id)
    assert reopened.list_workers() == []
    assert reopened.get(session.session_id).worker_confirmed is False
    assert (root.stat().st_mode & 0o777) == 0o700
    assert (root / "manifest.sqlite").stat().st_mode & 0o777 == 0o600
    assert not (tmp_path / "edited").exists()
    reopened.close()


def test_recording_worker_resets_confirmation_and_requires_a_name(registry, session):
    registry.create(session)
    with pytest.raises(ValueError):
        registry.worker_created(session.session_id)
    registry.record_worker(session.session_id, "worker-1")
    assert registry.worker_created(session.session_id).worker_confirmed is True
    assert registry.record_worker(session.session_id, "worker-1").worker_confirmed is False


def test_old_registry_migrates_workers_as_unconfirmed(tmp_path, session):
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    manifest = root / "manifest.sqlite"
    metadata = os.stat(session.cwd)
    with sqlite3.connect(manifest) as db:
        db.execute("""CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, runtime_kind TEXT NOT NULL,
            cwd TEXT NOT NULL, sandbox TEXT NOT NULL,
            directory_device INTEGER NOT NULL, directory_inode INTEGER NOT NULL,
            external_thread_id TEXT, worker_name TEXT UNIQUE,
            workspace_digest TEXT, home_digest TEXT
        )""")
        db.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, NULL, ?, NULL, NULL)",
            (
                session.session_id,
                session.runtime_kind,
                session.cwd,
                session.sandbox,
                metadata.st_dev,
                metadata.st_ino,
                "worker-before-upgrade",
            ),
        )
    manifest.chmod(0o600)
    registry = SessionRegistry(root)
    assert registry.list_workers()[0].worker_confirmed is False
    registry.worker_created(session.session_id)
    registry.close()
    reopened = SessionRegistry(root)
    assert reopened.list_workers()[0].worker_confirmed is True
    reopened.close()


def test_session_identity_and_thread_cannot_be_rebound(registry, session):
    record = registry.create(session)
    assert registry.create(session) == record
    registry.bind_thread(session.session_id, "thread-1")
    assert (
        registry.create(replace(session, external_thread_id="thread-1")).external_thread_id
        == "thread-1"
    )
    for changed in [
        replace(session, sandbox="read_only"),
        replace(session, runtime_kind="other"),
        replace(session, external_thread_id="thread-2"),
    ]:
        with pytest.raises(ValueError):
            registry.create(changed)
    with pytest.raises(ValueError):
        registry.create(session, identity=(record.directory_device, record.directory_inode + 1))
    with pytest.raises(ValueError):
        registry.bind_thread(session.session_id, "thread-2")


def test_replaced_project_identity_is_rejected(registry, session, tmp_path):
    registry.create(session)
    os.rename(session.cwd, tmp_path / "old")
    os.mkdir(session.cwd)
    with pytest.raises(ValueError):
        registry.create(session)


@pytest.mark.parametrize(
    "session_id", ["../outside", "/absolute", "bad/name", "bad\\name", "", ".", "..", "a\x00b"]
)
def test_rejects_path_injection_session_ids(registry, session, session_id):
    with pytest.raises(ValueError):
        registry.get(session_id)
    if session_id:
        with pytest.raises(ValueError):
            registry.create(replace(session, session_id=session_id))


def test_rejects_broad_access_and_symlink_project(registry, session, tmp_path):
    with pytest.raises(ValueError):
        registry.create(replace(session, sandbox="broad_access"))
    link = tmp_path / "link"
    link.symlink_to(session.cwd, target_is_directory=True)
    with pytest.raises(ValueError):
        registry.create(replace(session, cwd=str(link)))


def test_registry_refuses_unsafe_roots_and_metadata(tmp_path):
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o755)
    with pytest.raises(ValueError):
        SessionRegistry(unsafe)
    link = tmp_path / "link"
    link.symlink_to(unsafe, target_is_directory=True)
    with pytest.raises(ValueError):
        SessionRegistry(link / "state")
    root = tmp_path / "state"
    root.mkdir(mode=0o700)
    (root / "manifest.sqlite").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        SessionRegistry(root)
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize(
    "column,value",
    [
        ("cwd", "../relative"),
        ("directory_inode", -1),
        ("runtime_kind", ""),
        ("external_thread_id", "bad\x00thread"),
    ],
)
def test_corrupt_identity_is_refused(tmp_path, registry, session, column, value):
    registry.create(session)
    with sqlite3.connect(tmp_path / "state" / "manifest.sqlite") as db:
        db.execute(f"UPDATE sessions SET {column} = ?", (value,))  # noqa: S608 -- fixed test parameters
    with pytest.raises(ValueError):
        registry.get(session.session_id)


def test_invalid_checkpoint_preserves_previous_pair_and_thread(registry, session):
    registry.create(session)
    old = archive()
    registry.checkpoint(session.session_id, old, old, thread_id="thread-1")
    with pytest.raises(ValueError):
        registry.checkpoint(session.session_id, archive(("new", tarfile.REGTYPE, b"new")), b"bad")
    assert registry.archives(session.session_id) == (old, old)
    assert registry.get(session.session_id).external_thread_id == "thread-1"


def test_failed_second_blob_write_keeps_previous_checkpoint(registry, session, monkeypatch):
    registry.create(session)
    old = archive()
    registry.checkpoint(session.session_id, old, old)
    actual = registry._write_blob
    calls = 0

    def fail_second(blob):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return actual(blob)

    monkeypatch.setattr(registry, "_write_blob", fail_second)
    with pytest.raises(OSError):
        registry.checkpoint(
            session.session_id,
            archive(("new", tarfile.REGTYPE, b"new")),
            old,
            thread_id="thread-new",
        )
    assert registry.archives(session.session_id) == (old, old)
    assert registry.get(session.session_id).external_thread_id is None


def test_checkpoint_database_failure_rolls_back_both_refs(tmp_path, registry, session):
    registry.create(session)
    old = archive()
    registry.checkpoint(session.session_id, old, old)
    with sqlite3.connect(tmp_path / "state" / "manifest.sqlite") as db:
        db.execute(
            "CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON sessions BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
    with pytest.raises(ValueError):
        registry.checkpoint(
            session.session_id,
            archive(("new", tarfile.REGTYPE, b"new")),
            old,
            thread_id="thread-new",
        )
    assert registry.archives(session.session_id) == (old, old)
    assert registry.get(session.session_id).external_thread_id is None


@pytest.mark.parametrize("corruption", ["contents", "symlink", "hardlink", "missing"])
def test_corrupt_blob_is_refused(tmp_path, registry, session, corruption):
    registry.create(session)
    record = registry.checkpoint(session.session_id, archive(), archive())
    blob = tmp_path / "state" / "blobs" / record.workspace_digest
    if corruption == "contents":
        blob.write_bytes(b"tampered")
    elif corruption == "symlink":
        blob.unlink()
        blob.symlink_to(tmp_path / "outside")
    elif corruption == "hardlink":
        os.link(blob, tmp_path / "outside")
    else:
        blob.unlink()
    with pytest.raises(ValueError):
        registry.archives(session.session_id)


def test_missing_sessions_and_duplicate_worker_names(registry, session):
    assert registry.get("missing") is None
    with pytest.raises(KeyError):
        registry.archives("missing")
    registry.create(session)
    assert registry.archives(session.session_id) is None
    registry.record_worker(session.session_id, "worker-1")
    other = replace(session, session_id="session-2")
    registry.create(other)
    with pytest.raises(ValueError):
        registry.record_worker(other.session_id, "worker-1")
    with pytest.raises(ValueError):
        registry.record_worker(session.session_id, "worker-2")


def test_baseline_is_immutable_and_survives_checkpoint_and_reopen(registry, session):
    registry.create(session)
    first = archive(("a", tarfile.REGTYPE, b"original"))
    later = archive(("a", tarfile.REGTYPE, b"changed"))
    registry.record_baseline(session.session_id, first, base_commit="a" * 40)
    registry.checkpoint(session.session_id, later, archive())
    with pytest.raises(ValueError, match="immutable"):
        registry.record_baseline(session.session_id, later, base_commit="a" * 40)
    root = registry.root
    registry.close()
    reopened = SessionRegistry(root)
    try:
        assert reopened.baseline(session.session_id) == (first, "a" * 40)
        assert reopened.archives(session.session_id)[0] == later
    finally:
        reopened.close()
