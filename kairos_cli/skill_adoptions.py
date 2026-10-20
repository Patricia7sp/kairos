"""Composição síncrona dos comandos públicos de adoção de skills."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_skills.adoptions import SkillAdoptionService
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    validate_actor,
    validate_id,
    validate_name,
)
from kairos_skills.org_policy import observe_org_policy, org_policy_context
from kairos_skills.removal_state import database_connection
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


def _identification_metadata(result) -> dict[str, object]:
    record = result.record
    identification = result.identification
    operation_id = None if record is None else record.operation_id
    adoption_id = identification.adoption_id
    installation_id = identification.current_installation_id
    return {
        "id": operation_id,
        "operation_id": operation_id,
        "name": identification.name,
        "action": "adopt",
        "state": "preexisting" if record is None else record.state.value,
        "record": (
            None
            if record is None
            else {"operation_id": record.operation_id, "state": record.state.value}
        ),
        "installation_id": installation_id,
        "adoption_id": adoption_id,
        "lineage": {
            "installation_id": installation_id,
            "adoption_id": adoption_id,
            "valid": identification.identity_valid,
        },
        "validity": {
            "identity_valid": identification.identity_valid,
            "adoption_active": identification.adoption_active,
        },
        "identity_valid": identification.identity_valid,
        "adoption_active": identification.adoption_active,
        "historical_creation_origin": identification.historical_creation_origin,
        "installation_source": identification.installation_source,
        "authorization_basis": identification.authorization_basis,
    }


def _write_adoption(home: Path, args: argparse.Namespace, *, policy) -> dict[str, object]:
    command = args.skills_command
    with database_connection(home, write=True) as connection:
        migrate(connection)
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        service = SkillAdoptionService(home, repository, policy=policy)
        if command == "adopt":
            return _identification_metadata(
                service.adopt(args.name, actor=Actor.USER_FOREGROUND, confirmed=True)
            )
        record = service.unadopt(
            args.name,
            actor=Actor.USER_FOREGROUND,
            confirmed=True,
            expect_adoption=args.expect_adoption,
        )

    from kairos_cli.skill_mutations import mutation_metadata

    return mutation_metadata(record)


def _validate_adoption_args(args: argparse.Namespace) -> str:
    command = getattr(args, "skills_command", None)
    if command not in {"adopt", "unadopt"}:
        raise SkillMutationError("input", "Comando de adoção inválido.")
    validate_actor(Actor.USER_FOREGROUND)
    if getattr(args, "yes", False) is not True:
        raise SkillMutationError("denied", "Ação exige confirmação explícita --yes.")
    validate_name(getattr(args, "name", None))
    expect_adoption = getattr(args, "expect_adoption", None)
    if command == "unadopt" and expect_adoption is not None:
        try:
            validate_id(expect_adoption)
        except SkillMutationError:
            raise SkillMutationError(
                "input", "--expect-adoption exige um ID de adoção válido."
            ) from None
    if command == "adopt" and expect_adoption is not None:
        raise SkillMutationError("input", "--expect-adoption só é aceito em unadopt.")
    return command


def _emit_metadata(metadata: dict[str, object], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(metadata, ensure_ascii=False, indent=2))
        return
    for field, value in metadata.items():
        print(f"{field}: {value}")
    print(
        "A adoção ou revogação altera somente a autoridade; os arquivos e snapshots são preservados."
    )


def run_skill_adoption(home: Path, args: argparse.Namespace) -> int:
    """Executa A/U após validar os guards lexicais antes de qualquer I/O."""
    try:
        _validate_adoption_args(args)
        with observe_org_policy(org_policy_context(os.environ), home) as policy:
            metadata = _write_adoption(home, args, policy=policy)
        _emit_metadata(metadata, as_json=getattr(args, "json", False))
        return 0
    except SkillMutationError as error:
        message = str(error)
        if error.kind == "unavailable":
            message = "Primitiva segura indisponível; arquivos preservados."
        elif error.kind == "io":
            message = (
                "Não foi possível acessar o estado de skills com segurança; arquivos preservados."
            )
        print("kairos: " + message, file=sys.stderr)
        return {"input": 2, "unavailable": 69, "denied": 77}.get(error.kind, 1)
    except (OSError, sqlite3.DatabaseError):
        print(
            "kairos: não foi possível acessar o estado de skills com segurança; arquivos preservados.",
            file=sys.stderr,
        )
        return 1
