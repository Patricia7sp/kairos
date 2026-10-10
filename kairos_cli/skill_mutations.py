"""Composição síncrona e metadata pública dos comandos de autoria."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    require,
    validate_actor,
    validate_id,
    validate_name,
)
from kairos_skills.mutation_io import (
    read_skill_creation,
)
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import CommonRecord, SkillTreeAction, SkillTreeRecord
from kairos_skills.removal_state import database_connection, read_skill_repository
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


@contextmanager
def _database_connection(home: Path, *, write: bool = False):
    with database_connection(home, write=write) as connection:
        yield connection


def _history(home: Path, args):
    with _database_connection(home) as connection:
        if connection is None:
            return ()
        repository = read_skill_repository(connection, home)
        return () if repository is None else repository.history(name=args.name, limit=args.limit)


def _emit(records, *, as_json: bool, history: bool):
    if as_json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return
    if history and not records:
        print("nenhuma operação de autoria registrada")
        return
    for record in records if history else (records,):
        print(" ".join(f"{key}={value}" for key, value in record.items()))


def _validate_mutation(home: Path, args):
    command = args.skills_command
    validate_actor(Actor.USER_FOREGROUND)
    if command == "remove":
        if args.yes is not True:
            raise SkillMutationError("denied", "Remoção exige confirmação explícita --yes.")
        validate_name(args.name)
    elif command == "add":
        source = Path(args.file)
        read_skill_creation(source)
        return source
    elif command == "rollback":
        validate_id(args.operation_id)
        with _database_connection(home) as reader:
            repository = None if reader is None else read_skill_repository(reader, home)
            existing = None if repository is None else repository.get(args.operation_id)
            if existing is None:
                raise SkillMutationError(
                    "conflict",
                    "Operação não encontrada neste histórico.",
                    operation_id=args.operation_id,
                )
    else:
        raise SkillMutationError("input", "Comando de autoria inválido.")
    return None


def _write_mutation(home: Path, args, source):
    command = args.skills_command
    with _database_connection(home, write=True) as connection:
        migrate(connection)
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        service = SkillMutationService(home, repository)
        if command == "add":
            record = service.add(source, actor=Actor.USER_FOREGROUND)
        elif command == "remove":
            record = service.remove(args.name, actor=Actor.USER_FOREGROUND, confirmed=True)
        else:
            record = service.rollback(args.operation_id, actor=Actor.USER_FOREGROUND)
        records = mutation_metadata(record)
        if isinstance(record, SkillTreeRecord):
            records.update({"nome": record.name, "operation_id": record.operation_id})
            if record.action is SkillTreeAction.REMOVE:
                completed = record.state.value == "committed"
                records.update({"removido": completed, "restauravel": completed})
    return records


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
            source = _validate_mutation(home, args)
            records = _write_mutation(home, args, source)
        _emit(records, as_json=args.json, history=command == "history")
        return 0
    except SkillMutationError as error:
        message = str(error)
        if error.operation_id is not None:
            message += f" Operação: {error.operation_id}. Consulte skills history."
        print("kairos: " + message, file=sys.stderr)
        return {"input": 2, "unavailable": 69, "denied": 77}.get(error.kind, 1)
    except (OSError, sqlite3.DatabaseError):
        print(
            "kairos: não foi possível acessar o estado de skills com segurança; arquivos preservados.",
            file=sys.stderr,
        )
        return 1
