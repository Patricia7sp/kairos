"""Estado local da autoridade de auditoria. Só a administração cria o store."""

from __future__ import annotations

import fcntl
import math
import os
import sqlite3
import stat
import time
import uuid
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

from kairos_filesystem.descriptors import open_directory, open_fd, same_entry

DB = "audit-authority.sqlite3"
LOCK = "audit-authority.lock"
SCHEMA_VERSION = 2
SCHEMA = """
CREATE TABLE policy (singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL);
INSERT INTO policy VALUES(1,1);
CREATE TABLE binding(lock_dev INTEGER, lock_ino INTEGER, db_dev INTEGER, db_ino INTEGER);
CREATE TABLE projects(id TEXT PRIMARY KEY, catalog TEXT NOT NULL UNIQUE, catalog_identity TEXT NOT NULL UNIQUE);
CREATE TABLE exports(project TEXT NOT NULL REFERENCES projects(id), revision TEXT NOT NULL,
    root_identity TEXT NOT NULL, fingerprint TEXT NOT NULL, PRIMARY KEY(project,revision));
CREATE TABLE grants(id TEXT PRIMARY KEY, principal TEXT NOT NULL, project TEXT NOT NULL,
    revision TEXT NOT NULL, action TEXT NOT NULL, epoch INTEGER NOT NULL, expires INTEGER NOT NULL,
    issued REAL NOT NULL, deadline REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(project,revision) REFERENCES exports(project,revision));
"""
EXCEPTION_SCHEMA = """
DROP TABLE grants;
CREATE TABLE grants (id TEXT PRIMARY KEY, principal TEXT NOT NULL, project TEXT NOT NULL,
    revision TEXT NOT NULL, action TEXT NOT NULL, epoch INTEGER NOT NULL, expires INTEGER NOT NULL,
    issued REAL NOT NULL, deadline INTEGER NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
    boot_id TEXT NOT NULL, issued_boot_ns INTEGER NOT NULL,
    FOREIGN KEY(project,revision) REFERENCES exports(project,revision));
CREATE INDEX audit_grant_scope ON grants(principal, project, revision, action, epoch);
CREATE TABLE audit_proofs (
    id TEXT PRIMARY KEY, principal TEXT NOT NULL, read_grant TEXT NOT NULL,
    project TEXT NOT NULL, revision TEXT NOT NULL, export_identity TEXT NOT NULL,
    fingerprint TEXT NOT NULL, profile TEXT NOT NULL, profile_version INTEGER NOT NULL,
    policy_epoch INTEGER NOT NULL, created_at REAL NOT NULL, created_boot_ns INTEGER NOT NULL,
    boot_id TEXT NOT NULL, projection TEXT NOT NULL);
CREATE TABLE audit_intents (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES audit_proofs(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK(action='approve_audit_exception'), principal TEXT NOT NULL,
    finding_key TEXT NOT NULL, approve_grant TEXT NOT NULL, origin TEXT NOT NULL,
    transport TEXT NOT NULL, policy_epoch INTEGER NOT NULL, policy_version INTEGER NOT NULL,
    reason_code TEXT NOT NULL, expires_at INTEGER NOT NULL, issued_at REAL NOT NULL,
    issued_boot_ns INTEGER NOT NULL, deadline_boot_ns INTEGER NOT NULL, boot_id TEXT NOT NULL,
    consumed_at REAL);
CREATE TABLE audit_exceptions (
    id TEXT PRIMARY KEY, intent_id TEXT NOT NULL UNIQUE REFERENCES audit_intents(id) ON DELETE CASCADE,
    proof_run_id TEXT NOT NULL REFERENCES audit_proofs(id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK(action='approve_audit_exception'), principal TEXT NOT NULL,
    finding_key TEXT NOT NULL, approve_grant TEXT NOT NULL, read_grant TEXT NOT NULL,
    policy_epoch INTEGER NOT NULL, policy_version INTEGER NOT NULL, reason_code TEXT NOT NULL,
    issued_at REAL NOT NULL, issued_boot_ns INTEGER NOT NULL, boot_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL, expires_boot_ns INTEGER NOT NULL, revoked_at REAL,
    revoked_by TEXT, revoke_grant TEXT, revocation_reason TEXT);
CREATE INDEX audit_exception_match ON audit_exceptions(principal, finding_key, policy_epoch);
CREATE TABLE audit_assessments (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES audit_proofs(id) ON DELETE CASCADE,
    policy_outcome TEXT NOT NULL, coverage_complete INTEGER NOT NULL, threshold TEXT NOT NULL,
    applied_ids TEXT NOT NULL, evaluated_at REAL NOT NULL, evaluated_boot_ns INTEGER NOT NULL,
    commit_confirmed INTEGER NOT NULL CHECK(commit_confirmed=1));
PRAGMA user_version=2;
"""


class AuthorityDenied(PermissionError):
    def __init__(self):
        super().__init__("Auditoria não autorizada.")


def require(condition):
    if not condition:
        raise AuthorityDenied()


def _private(fd, *, directory=False):
    s = os.fstat(fd)
    require(s.st_uid == os.geteuid() and not s.st_mode & 0o077)
    require(
        stat.S_ISDIR(s.st_mode)
        if directory
        else stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o600
    )


def boot_seconds():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


def boot_id():
    return str(
        uuid.UUID(Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip())
    )


_NS = 10**9
_MAX_CLOCK_SKEW_SECONDS = 5


@dataclass(frozen=True, slots=True)
class Clock:
    utc: float
    boot_ns: int
    generation: str

    @classmethod
    def read(cls):
        utc, boot = time.time(), boot_seconds()
        require(math.isfinite(utc) and utc > 0 and math.isfinite(boot) and boot >= 0)
        return cls(utc, int(boot * _NS), boot_id())

    def deadline(self, expires_at):
        return self.boot_ns + int((expires_at - self.utc) * _NS)

    def within(self, generation, issued_at, issued_boot_ns, expires_at, deadline_boot_ns):
        elapsed = self.utc - issued_at
        elapsed_boot = (self.boot_ns - issued_boot_ns) / _NS
        return (
            generation == self.generation
            and elapsed >= 0
            and elapsed_boot >= 0
            and abs(elapsed - elapsed_boot) <= _MAX_CLOCK_SKEW_SECONDS
            and self.utc < expires_at
            and self.boot_ns < deadline_boot_ns
        )


def _schema(conn, ddl):
    for statement in ddl.strip().rstrip(";").split(";"):
        conn.execute(statement)


class AuthorityStore:
    def __init__(self, home: Path):
        require(home.is_absolute())
        self.home = home

    @contextmanager
    def connection(self, *, initialize=False, write=False):
        with self._connection(initialize=initialize, write=write) as (conn, _):
            yield conn

    @contextmanager
    def _connection(self, *, initialize=False, write=False, migrate=False, restore=False):
        with ExitStack() as stack:
            directory, _ = open_directory(stack, self.home)
            _private(directory, directory=True)
            flags = os.O_NONBLOCK | os.O_RDWR
            if initialize:
                flags |= os.O_CREAT | os.O_EXCL
            lock = open_fd(stack, LOCK, flags, parent=directory)
            _private(lock)
            fcntl.flock(lock, fcntl.LOCK_EX)
            same_entry(directory, LOCK, lock)
            fd = open_fd(stack, DB, flags, parent=directory)
            _private(fd)
            mode = "rw" if write or initialize else "ro"
            uri = f"file:/proc/self/fd/{directory}/{DB}?mode={mode}"
            conn = stack.enter_context(closing(sqlite3.connect(uri, uri=True, timeout=0)))
            conn.row_factory = sqlite3.Row
            same_entry(directory, DB, fd)
            conn.execute("PRAGMA foreign_keys=ON")
            lock_stat, db_stat = os.fstat(lock), os.fstat(fd)
            binding = (lock_stat.st_dev, lock_stat.st_ino, db_stat.st_dev, db_stat.st_ino)
            with conn:
                if write or initialize:
                    conn.execute("PRAGMA synchronous=FULL")
                    conn.execute("BEGIN IMMEDIATE")
                if initialize:
                    _schema(conn, SCHEMA)
                    _schema(conn, EXCEPTION_SCHEMA)
                    conn.execute("INSERT INTO binding VALUES(?,?,?,?)", binding)
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                require(version == (1 if migrate else SCHEMA_VERSION))
                if not restore:
                    require(
                        [tuple(r) for r in conn.execute("SELECT * FROM binding LIMIT 2")]
                        == [binding]
                    )
                yield conn, binding

    def initialize(self):
        with self.connection(initialize=True):
            pass

    def migrate(self):
        """Operação local offline de v1 para v2. Grants antigos não têm geração de boot."""
        with self._connection(write=True, migrate=True) as (conn, _):
            _schema(conn, EXCEPTION_SCHEMA)

    def reset_after_restore(self):
        """Operação local offline antes de servir um backup instalado em novo inode."""
        with self._connection(write=True, restore=True) as (conn, binding):
            old = [tuple(r) for r in conn.execute("SELECT * FROM binding LIMIT 2")]
            require(len(old) == 1 and old[0][2:] != binding[2:])
            require(conn.execute("UPDATE policy SET epoch=epoch+1 WHERE singleton=1").rowcount == 1)
            conn.execute(
                """UPDATE audit_exceptions SET revoked_at=?,revoked_by='local:restore',
                revocation_reason='authority_restored' WHERE revoked_at IS NULL""",
                (Clock.read().utc,),
            )
            conn.execute("DELETE FROM grants")
            conn.execute("DELETE FROM exports")
            conn.execute("DELETE FROM projects")
            conn.execute("DELETE FROM binding")
            conn.execute("INSERT INTO binding VALUES(?,?,?,?)", binding)
