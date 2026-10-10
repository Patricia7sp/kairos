"""Abertura segura compartilhada e leitura de exclusões sem migração."""

from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.mutation_io import check_chain, open_directory, open_fd, same_entry
from kairos_state.connection import BUSY_TIMEOUT_MS, apply_wal_with_fallback
from kairos_state.repositories.skill_mutations import SkillMutationRepository, corrupt_errors
from kairos_state.skill_writer import register_skill_writer


def _database_entry(directory: int, chain) -> bool:
    found = False
    for name in ("state.db", "state.db-wal", "state.db-shm", "state.db-journal"):
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise SkillMutationError("conflict", "Entrada do banco insegura; operação recusada.")
        found |= name == "state.db"
    check_chain(chain)
    return found


@contextmanager
def database_connection(home: Path, *, write: bool = False):
    with ExitStack() as stack:
        try:
            directory, chain = open_directory(stack, home, create=write)
        except FileNotFoundError:
            yield None
            return
        if not _database_entry(directory, chain) and not write:
            yield None
            return
        flags = (os.O_RDWR | os.O_CREAT) if write else os.O_RDONLY
        fd = open_fd(stack, "state.db", flags | os.O_NONBLOCK, parent=directory)
        observed = os.fstat(fd)
        if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
            raise SkillMutationError("conflict", "Entrada do banco insegura; operação recusada.")
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)
        descriptor_path = Path(f"/proc/self/fd/{fd}")
        if not descriptor_path.exists():
            raise SkillMutationError("unavailable", "Abertura segura do banco indisponível.")
        mode = "rw" if write else "ro"
        connection = sqlite3.connect(
            descriptor_path.as_uri() + "?mode=" + mode,
            uri=True,
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
        stack.callback(connection.close)
        connection.row_factory = sqlite3.Row
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)
        opened_path = connection.execute("PRAGMA database_list").fetchone()[2]
        opened = os.stat(opened_path, follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (observed.st_dev, observed.st_ino):
            raise SkillMutationError("conflict", "Banco alterado durante abertura; recusado.")
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        if write:
            register_skill_writer(connection)
            connection.execute("PRAGMA foreign_keys=ON")
            apply_wal_with_fallback(connection)
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)
        yield connection
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)


_LEGACY_TABLES = frozenset(
    {"skill_mutation_operations", "skill_mutation_events", "skill_mutation_contents"}
)
_TREE_TABLES = frozenset(
    {
        "skill_tree_operations",
        "skill_tree_events",
        "skill_tree_snapshots",
        "skill_tree_blobs",
        "skill_mutation_timeline",
    }
)


def read_skill_repository(connection: sqlite3.Connection, home: Path):
    """Reconhece homes antigos e recusa famílias parciais ou schema incompatível."""
    with corrupt_errors():
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = None
        if "schema_version" in names:
            versions = connection.execute("SELECT version FROM schema_version").fetchall()
            if (
                len(versions) != 1
                or type(versions[0][0]) is not int
                or not 0 <= versions[0][0] <= 8
            ):
                raise SkillMutationError(
                    "corrupt", "Versão do ledger incompatível; leitura recusada."
                )
            version = versions[0][0]
        families = names & (_LEGACY_TABLES | _TREE_TABLES)
        if not families:
            if version is not None and version >= 7:
                raise SkillMutationError(
                    "corrupt", "Ledger de skills ausente em banco migrado; leitura recusada."
                )
            return None
        if names & _TREE_TABLES or version == 8:
            if not names >= _TREE_TABLES | _LEGACY_TABLES or version != 8:
                raise SkillMutationError(
                    "corrupt", "Ledger de skills incompleto; leitura recusada."
                )
        elif not names >= _LEGACY_TABLES:
            raise SkillMutationError("corrupt", "Ledger de skills incompleto; leitura recusada.")
        return SkillMutationRepository(connection, home_id=catalog_home_id(home))


def read_skill_tombstones(home: Path) -> frozenset[str]:
    """Valida a projeção inteira e recusa pendências antes de permitir seeding."""
    with database_connection(home) as connection:
        if connection is None:
            return frozenset()
        repository = read_skill_repository(connection, home)
        if repository is None:
            return frozenset()
        with corrupt_errors():
            names = {
                row[0]
                for row in connection.execute("SELECT DISTINCT name FROM skill_mutation_operations")
            }
            modern = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='skill_tree_operations'"
            ).fetchone()
            if modern is not None:
                names.update(
                    row[0]
                    for row in connection.execute("SELECT DISTINCT name FROM skill_tree_operations")
                )
            tombstones = repository.bundled_tombstones()
        for name in sorted(names):
            pending = repository.pending(name)
            if pending:
                raise SkillMutationError(
                    "conflict",
                    "Existe operação pendente de skills; resolva seu ID antes do sync.",
                    operation_id=pending[0].operation_id,
                )
        return tombstones
