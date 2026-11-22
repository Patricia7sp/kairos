"""Estado local da autoridade de auditoria. Só a administração cria o store."""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from kairos_filesystem.descriptors import open_directory, open_fd, same_entry

DB = "audit-authority.sqlite3"
LOCK = "audit-authority.lock"
SCHEMA = """
BEGIN IMMEDIATE;
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
PRAGMA user_version=1;
COMMIT;
"""


class AuthorityDenied(PermissionError):
    def __init__(self):
        super().__init__("Leitura de auditoria não autorizada.")


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


class AuthorityStore:
    def __init__(self, home: Path):
        require(home.is_absolute())
        self.home = home

    @contextmanager
    def connection(self, *, initialize=False, write=False):
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
            if initialize:
                conn.executescript(SCHEMA)
                conn.execute("INSERT INTO binding VALUES(?,?,?,?)", binding)
            require(conn.execute("PRAGMA user_version").fetchone()[0] == 1)
            require([tuple(r) for r in conn.execute("SELECT * FROM binding LIMIT 2")] == [binding])
            with conn:
                yield conn

    def initialize(self):
        with self.connection(initialize=True):
            pass
