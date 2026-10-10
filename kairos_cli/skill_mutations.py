"""Composição síncrona e metadata pública dos comandos de autoria."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import stat
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    require,
    validate_id,
    validate_name,
)
from kairos_skills.mutation_io import (
    check_chain,
    open_directory,
    open_fd,
    read_skill_creation,
    same_entry,
)
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import CommonRecord, SkillTreeRecord
from kairos_state.connection import BUSY_TIMEOUT_MS, apply_wal_with_fallback
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def mutation_metadata(record: CommonRecord) -> dict[str, object]:
    metadata = {
        "id": record.operation_id,
        "name": record.name,
        "action": record.action.value,
        "state": record.state.value,
        "origin": record.provenance.value,
        "created_at": record.draft.created_at,
        "reverts": record.reverts,
    }
    if isinstance(record, SkillTreeRecord):
        metadata["snapshot_id"] = record.draft.snapshot_id
    else:
        metadata["sha256"] = record.sha256
    return metadata


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
def _database_connection(home: Path, *, write: bool = False):
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
            connection.execute("PRAGMA foreign_keys=ON")
            apply_wal_with_fallback(connection)
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)
        yield connection
        same_entry(directory, "state.db", fd)
        _database_entry(directory, chain)


def _history(home: Path, args):
    with _database_connection(home) as connection:
        if connection is None:
            return ()
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('skill_mutation_operations','skill_mutation_events','skill_mutation_contents','skill_tree_operations','skill_tree_events','skill_tree_snapshots','skill_tree_blobs','skill_mutation_timeline')"
            )
        }
        if names & {
            "skill_tree_operations",
            "skill_tree_events",
            "skill_tree_snapshots",
            "skill_tree_blobs",
            "skill_mutation_timeline",
        }:
            return SkillMutationRepository(connection, home_id=catalog_home_id(home)).history(
                name=args.name, limit=args.limit
            )
        if not names:
            version_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if version_table is not None:
                version = connection.execute("SELECT version FROM schema_version").fetchone()
                if version is not None and version[0] >= 7:
                    raise SkillMutationError(
                        "corrupt", "Ledger de skills ausente em banco migrado; leitura recusada."
                    )
            return ()
        if names != {
            "skill_mutation_operations",
            "skill_mutation_events",
            "skill_mutation_contents",
        }:
            raise SkillMutationError("corrupt", "Ledger de skills incompleto; leitura recusada.")
        return SkillMutationRepository(connection, home_id=catalog_home_id(home)).history(
            name=args.name, limit=args.limit
        )


def _emit(records, *, as_json: bool, history: bool):
    if as_json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    if history and not records:
        print("nenhuma operação de autoria registrada")
        return
    for record in records if history else (records,):
        print(" ".join(f"{key}={value}" for key, value in record.items()))


def run_skill_mutation(home: Path, args: argparse.Namespace) -> int:
    try:
        command = args.skills_command
        if command == "history":
            require(
                type(args.limit) is int and 1 <= args.limit <= 100, "Limite deve ser de 1 a 100."
            )
            if args.name is not None:
                validate_name(args.name)
            records = [mutation_metadata(record) for record in _history(home, args)]
        else:
            if command == "add":
                source = Path(args.file)
                read_skill_creation(source)
            elif command == "rollback":
                validate_id(args.operation_id)
            else:
                raise SkillMutationError("input", "Comando de autoria inválido.")
            with _database_connection(home, write=True) as connection:
                migrate(connection)
                repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
                service = SkillMutationService(home, repository)
                record = (
                    service.add(source, actor=Actor.USER_FOREGROUND)
                    if command == "add"
                    else service.rollback(args.operation_id, actor=Actor.USER_FOREGROUND)
                )
                records = mutation_metadata(record)
        _emit(records, as_json=args.json, history=command == "history")
        return 0
    except SkillMutationError as error:
        message = str(error)
        if error.operation_id is not None:
            message += f" Operação: {error.operation_id}. Consulte skills history."
        print("kairos: " + message, file=sys.stderr)
        return {"input": 2, "unavailable": 69}.get(error.kind, 1)
    except (OSError, sqlite3.DatabaseError):
        print(
            "kairos: não foi possível acessar o estado de skills com segurança; arquivos preservados.",
            file=sys.stderr,
        )
        return 1
