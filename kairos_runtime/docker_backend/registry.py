"""Private SQLite session manifest and durable content-addressed tar blobs.

Workers never receive a host path to this store. Crash-aborted writes can
leave unreferenced blobs (each bounded); automatic garbage collection is not
performed, so operators should account for those in storage maintenance.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from kairos_runtime.contracts import RuntimeSession

from .archives import MAX_ARCHIVE_BYTES, validate_archive

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError("invalid session or worker identifier")
    return value


def _directory(path: Path, *, create: bool = False) -> int:
    absolute = Path(os.path.abspath(path))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                    os.fsync(fd)
                except FileExistsError:
                    pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except OSError as exc:
        os.close(fd)
        raise ValueError("unsafe or inaccessible directory") from exc


def _private(metadata: os.stat_result, *, directory: bool = False) -> None:
    expected_mode = 0o700 if directory else 0o600
    correct_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
    if (
        not correct_type
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != expected_mode
        or (not directory and metadata.st_nlink != 1)
    ):
        raise ValueError("registry must be private and operator-owned")


@dataclass(frozen=True)
class SessionRecord:
    session_id: str
    runtime_kind: str
    cwd: str
    sandbox: str
    directory_device: int
    directory_inode: int
    external_thread_id: str | None
    worker_name: str | None
    workspace_digest: str | None
    home_digest: str | None
    worker_confirmed: bool = False


class SessionRegistry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._root_fd = _directory(self.root, create=True)
        self._blobs_fd = -1
        try:
            _private(os.fstat(self._root_fd), directory=True)
            try:
                os.mkdir("blobs", 0o700, dir_fd=self._root_fd)
            except FileExistsError:
                pass
            self._blobs_fd = os.open(
                "blobs", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._root_fd
            )
            _private(os.fstat(self._blobs_fd), directory=True)
            fd = os.open(
                "manifest.sqlite",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
                dir_fd=self._root_fd,
            )
            try:
                _private(os.fstat(fd))
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(self._root_fd)
            with self._connection() as db:
                db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    runtime_kind TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    sandbox TEXT NOT NULL CHECK(sandbox IN ('read_only', 'workspace_write')),
                    directory_device INTEGER NOT NULL,
                    directory_inode INTEGER NOT NULL,
                    external_thread_id TEXT,
                    worker_name TEXT UNIQUE,
                    workspace_digest TEXT,
                    home_digest TEXT,
                    worker_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(worker_confirmed IN (0, 1)),
                    CHECK((workspace_digest IS NULL) = (home_digest IS NULL))
                )""")
                db.execute("""CREATE TABLE IF NOT EXISTS baselines (
                    session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
                    workspace_digest TEXT NOT NULL,
                    base_commit TEXT
                )""")
                columns = {row["name"] for row in db.execute("PRAGMA table_info(sessions)")}
                if "worker_confirmed" not in columns:
                    # An older record proves only that creation was requested.
                    # Never infer successful daemon creation during migration.
                    db.execute(
                        "ALTER TABLE sessions ADD COLUMN worker_confirmed INTEGER NOT NULL DEFAULT 0 CHECK(worker_confirmed IN (0, 1))"
                    )
        except (OSError, ValueError):
            self.close()
            raise ValueError("unsafe or corrupt session registry") from None

    @contextmanager
    def _connection(self):
        with self._lock:
            if self._root_fd < 0:
                raise ValueError("session registry is closed")
            for name in (
                "manifest.sqlite",
                "manifest.sqlite-journal",
                "manifest.sqlite-wal",
                "manifest.sqlite-shm",
            ):
                try:
                    _private(os.stat(name, dir_fd=self._root_fd, follow_symlinks=False))
                except FileNotFoundError:
                    if name == "manifest.sqlite":
                        raise ValueError("registry manifest is missing") from None
            db = None
            try:
                db = sqlite3.connect(f"/proc/self/fd/{self._root_fd}/manifest.sqlite")
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA synchronous=FULL")
                db.execute("PRAGMA journal_mode=DELETE")
                with db:
                    yield db
            except sqlite3.DatabaseError as exc:
                raise ValueError("session registry operation failed") from exc
            finally:
                if db is not None:
                    db.close()

    @staticmethod
    def _record(row) -> SessionRecord:
        fields = dict(row)
        confirmed = fields["worker_confirmed"]
        if type(confirmed) is not int or confirmed not in {0, 1}:
            raise ValueError("corrupt worker creation state")
        fields["worker_confirmed"] = bool(confirmed)
        record = SessionRecord(**fields)
        _identifier(record.session_id)
        if (
            not isinstance(record.cwd, str)
            or "\x00" in record.cwd
            or not Path(record.cwd).is_absolute()
            or str(Path(record.cwd)) != record.cwd
            or ".." in Path(record.cwd).parts
            or not isinstance(record.runtime_kind, str)
            or not record.runtime_kind.strip()
            or type(record.directory_device) is not int
            or record.directory_device < 0
            or type(record.directory_inode) is not int
            or record.directory_inode <= 0
        ):
            raise ValueError("corrupt session identity")
        if record.external_thread_id is not None:
            SessionRegistry._thread(record, record.external_thread_id)
        if record.sandbox not in {"read_only", "workspace_write"}:
            raise ValueError("corrupt sandbox identity")
        if record.worker_name is not None:
            _identifier(record.worker_name)
        elif record.worker_confirmed:
            raise ValueError("confirmed worker has no identity")
        if (record.workspace_digest is None) != (record.home_digest is None):
            raise ValueError("partial checkpoint")
        for digest in (record.workspace_digest, record.home_digest):
            if digest is not None and not _DIGEST.fullmatch(digest):
                raise ValueError("corrupt checkpoint reference")
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (_identifier(session_id),)
            ).fetchone()
            return self._record(row) if row is not None else None

    def _require(self, session_id: str) -> SessionRecord:
        record = self.get(session_id)
        if record is None:
            raise KeyError(session_id)
        return record

    def create(
        self, session: RuntimeSession, *, identity: tuple[int, int] | None = None
    ) -> SessionRecord:
        _identifier(session.session_id)
        if session.sandbox not in {"read_only", "workspace_write"}:
            raise ValueError("broad_access is unavailable in Docker sessions")
        if (
            not Path(session.cwd).is_absolute()
            or str(Path(session.cwd)) != session.cwd
            or ".." in Path(session.cwd).parts
        ):
            raise ValueError("session cwd must be canonical and absolute")
        fd = _directory(Path(session.cwd))
        try:
            metadata = os.fstat(fd)
            captured = (metadata.st_dev, metadata.st_ino)
        finally:
            os.close(fd)
        if identity is not None and identity != captured:
            raise ValueError("project identity changed")
        with self._lock:
            existing = self.get(session.session_id)
            if existing is not None:
                if (
                    existing.cwd,
                    existing.runtime_kind,
                    existing.sandbox,
                    existing.directory_device,
                    existing.directory_inode,
                ) != (session.cwd, session.runtime_kind, session.sandbox, *captured) or (
                    session.external_thread_id is not None
                    and existing.external_thread_id != session.external_thread_id
                ):
                    raise ValueError("session identity cannot be rebound")
                return existing
            with self._connection() as db:
                db.execute(
                    "INSERT INTO sessions (session_id, runtime_kind, cwd, sandbox, directory_device, directory_inode, external_thread_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        session.session_id,
                        session.runtime_kind,
                        session.cwd,
                        session.sandbox,
                        *captured,
                        session.external_thread_id,
                    ),
                )
            return self._require(session.session_id)

    @staticmethod
    def _thread(record: SessionRecord, thread_id: str) -> None:
        if (
            not isinstance(thread_id, str)
            or not thread_id.strip()
            or len(thread_id) > 512
            or "\x00" in thread_id
        ):
            raise ValueError("invalid external thread identifier")
        if record.external_thread_id not in {None, thread_id}:
            raise ValueError("thread identity cannot be rebound")

    def bind_thread(self, session_id: str, thread_id: str) -> SessionRecord:
        with self._lock:
            self._thread(self._require(session_id), thread_id)
            with self._connection() as db:
                db.execute(
                    "UPDATE sessions SET external_thread_id = ? WHERE session_id = ?",
                    (thread_id, session_id),
                )
            return self._require(session_id)

    def record_worker(self, session_id: str, name: str) -> SessionRecord:
        _identifier(name)
        with self._lock:
            record = self._require(session_id)
            if record.worker_name not in {None, name}:
                raise ValueError("registered worker must be removed first")
            with self._connection() as db:
                db.execute(
                    "UPDATE sessions SET worker_name = ?, worker_confirmed = 0 WHERE session_id = ?",
                    (name, session_id),
                )
            return self._require(session_id)

    def worker_created(self, session_id: str) -> SessionRecord:
        """Durably acknowledge a completed, attested worker startup."""
        with self._lock:
            if self._require(session_id).worker_name is None:
                raise ValueError("worker must be recorded before creation is confirmed")
            with self._connection() as db:
                db.execute(
                    "UPDATE sessions SET worker_confirmed = 1 WHERE session_id = ?", (session_id,)
                )
            return self._require(session_id)

    def worker_removed(self, session_id: str) -> None:
        with self._lock:
            self._require(session_id)
            with self._connection() as db:
                db.execute(
                    "UPDATE sessions SET worker_name = NULL, worker_confirmed = 0 WHERE session_id = ?",
                    (session_id,),
                )

    def list_workers(self) -> list[SessionRecord]:
        with self._connection() as db:
            return [
                self._record(row)
                for row in db.execute(
                    "SELECT * FROM sessions WHERE worker_name IS NOT NULL ORDER BY session_id"
                )
            ]

    def checkpoint(
        self, session_id: str, workspace: bytes, home: bytes, *, thread_id: str | None = None
    ) -> SessionRecord:
        validate_archive(workspace)
        validate_archive(home)
        with self._lock:
            record = self._require(session_id)
            if thread_id is not None:
                self._thread(record, thread_id)
            workspace_digest = self._write_blob(workspace)
            home_digest = self._write_blob(home)
            with self._connection() as db:
                db.execute(
                    "UPDATE sessions SET workspace_digest = ?, home_digest = ?, external_thread_id = ? WHERE session_id = ?",
                    (
                        workspace_digest,
                        home_digest,
                        thread_id or record.external_thread_id,
                        session_id,
                    ),
                )
            return self._require(session_id)

    def record_baseline(
        self, session_id: str, workspace: bytes, *, base_commit: str | None = None
    ) -> None:
        """Persist the initial source once; retries may only supply identical values."""
        validate_archive(workspace)
        if base_commit is not None and (
            not isinstance(base_commit, str)
            or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_commit) is None
        ):
            raise ValueError("invalid baseline commit")
        with self._lock:
            self._require(session_id)
            existing = self.baseline(session_id)
            if existing is not None:
                if existing != (workspace, base_commit):
                    raise ValueError("baseline is immutable")
                return
            digest = self._write_blob(workspace)
            with self._connection() as db:
                db.execute(
                    "INSERT INTO baselines VALUES (?, ?, ?)", (session_id, digest, base_commit)
                )

    def baseline(self, session_id: str) -> tuple[bytes, str | None] | None:
        """Read the immutable archive and commit; None explicitly identifies legacy sessions."""
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM baselines WHERE session_id = ?", (_identifier(session_id),)
            ).fetchone()
            if row is None:
                return None
            commit = row["base_commit"]
            if (
                commit is not None
                and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit) is None
            ):
                raise ValueError("corrupt baseline commit")
            return self._read_blob(row["workspace_digest"]), commit

    def _write_blob(self, blob: bytes) -> str:
        digest = hashlib.sha256(blob).hexdigest()
        try:
            os.stat(digest, dir_fd=self._blobs_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            self._read_blob(digest)
            return digest
        temporary = f".pending-{uuid.uuid4().hex}"
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=self._blobs_fd,
        )
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(blob)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, digest, src_dir_fd=self._blobs_fd, dst_dir_fd=self._blobs_fd)
            os.fsync(self._blobs_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._blobs_fd)
            except FileNotFoundError:
                pass
        return digest

    def _read_blob(self, digest: str) -> bytes:
        if not _DIGEST.fullmatch(digest):
            raise ValueError("invalid checkpoint digest")
        try:
            fd = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._blobs_fd)
            with os.fdopen(fd, "rb") as source:
                metadata = os.fstat(source.fileno())
                _private(metadata)
                if metadata.st_size > MAX_ARCHIVE_BYTES:
                    raise ValueError("checkpoint archive too large")
                blob = source.read(MAX_ARCHIVE_BYTES + 1)
            if hashlib.sha256(blob).hexdigest() != digest:
                raise ValueError("checkpoint digest mismatch")
            return validate_archive(blob)
        except OSError as exc:
            raise ValueError("checkpoint archive inaccessible") from exc

    def archives(self, session_id: str) -> tuple[bytes, bytes] | None:
        with self._lock:
            record = self._require(session_id)
            if record.workspace_digest is None:
                return None
            return self._read_blob(record.workspace_digest), self._read_blob(record.home_digest)

    def close(self) -> None:
        with self._lock:
            for name in ("_blobs_fd", "_root_fd"):
                fd = getattr(self, name, -1)
                if fd >= 0:
                    os.close(fd)
                    setattr(self, name, -1)
