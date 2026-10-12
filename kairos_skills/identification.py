"""Diagnóstico por duas observações independentes, sem capacidades de escrita."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import ExitStack
from pathlib import Path

from kairos_domain.ownership import Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_filesystem.tree import observed_mount_id
from kairos_skills.identification_contract import InstallationEvidence, SkillIdentification
from kairos_skills.mutation_contract import SkillMutationError, validate_name
from kairos_skills.mutation_io import (
    _filesystem_errors,
    check_chain,
    io_errors,
    open_directory,
    open_fd,
)
from kairos_skills.removal_io import capture_skill
from kairos_skills.removal_origin import resolve_removal_origin
from kairos_skills.removal_state import database_connection, read_skill_repository


def _changed() -> SkillMutationError:
    return SkillMutationError("conflict", "Instalação ou ledger alterados durante a consulta.")


def read_installation_capture(home: Path, name: str) -> TreeCapture:
    """Abre somente diretórios existentes e revalida nomes e montagens."""
    validate_name(name)
    with io_errors(), _filesystem_errors(), ExitStack() as stack:
        try:
            installed, chain = open_directory(stack, Path(home) / "skills", create=False)
            mounts = {fd: observed_mount_id(fd) for _, _, fd in chain}
            mounts[installed] = observed_mount_id(installed)

            def check():
                check_chain(chain)
                if any(observed_mount_id(fd) != mount for fd, mount in mounts.items()):
                    raise _changed()
                with ExitStack() as observed:
                    for parent, component, fd in chain:
                        current = open_fd(observed, component, os.O_PATH, parent=parent)
                        if observed_mount_id(current) != mounts[fd]:
                            raise _changed()

            check()
            os.stat(name, dir_fd=installed, follow_symlinks=False)
            capture = capture_skill(installed, name)
            check()
            return capture
        except FileNotFoundError:
            raise SkillMutationError("conflict", "Instalação ausente; consulta recusada.") from None


def _database_fingerprint(connection: sqlite3.Connection) -> tuple[str | None, ...]:
    copied = Path(connection.execute("PRAGMA database_list").fetchone()[2])
    digests = []
    for path in (copied, copied.with_name(copied.name + "-wal")):
        try:
            with path.open("rb") as source:
                digests.append(hashlib.file_digest(source, "sha256").hexdigest())
        except FileNotFoundError:
            if path == copied:
                raise
            digests.append(None)
    return tuple(digests)


def _installation_evidence(
    home: Path, name: str, *, expected_database: tuple[str | None, ...] | None = None
) -> tuple[InstallationEvidence, tuple[str | None, ...]]:
    with io_errors():
        try:
            with database_connection(home, write=False) as connection:
                fingerprint = () if connection is None else _database_fingerprint(connection)
                if expected_database is not None and fingerprint != expected_database:
                    raise _changed()
                if connection is not None:
                    repository = read_skill_repository(connection, home)
                    if repository is not None:
                        return repository.installation_evidence(name), fingerprint
                return InstallationEvidence(None, None, None, ()), fingerprint
        except sqlite3.DatabaseError:
            raise SkillMutationError("io", "Não foi possível ler o ledger de skills.") from None


def _installation_changed_after_io(home: Path, name: str, captured: TreeCapture) -> bool:
    try:
        observed = read_installation_capture(home, name)
    except SkillMutationError as error:
        if error.kind == "conflict":
            return True
        if error.kind == "io":
            return False
        raise
    return observed != captured


def identify_skill(home: Path, name: str) -> SkillIdentification:
    """Diagnóstico do instante observado; remove deve revalidar suas próprias provas."""
    validate_name(name)
    home = Path(home)
    captured = None
    try:
        evidence, database = _installation_evidence(home, name)
        captured = read_installation_capture(home, name)
        origin = resolve_removal_origin(home, name, evidence.current, captured)
        repeated, _ = _installation_evidence(home, name, expected_database=database)
        recaptured = read_installation_capture(home, name)
        if repeated != evidence or recaptured != captured:
            raise _changed()
    except SkillMutationError as error:
        if (
            error.kind == "io"
            and captured is not None
            and _installation_changed_after_io(home, name, captured)
        ):
            error = _changed()
        if error.kind == "conflict":
            raise SkillMutationError(
                "conflict", str(error) + " Repita a consulta após verificar a instalação."
            ) from None
        raise

    provenance = origin.provenance
    pending = evidence.pending_operation_ids
    historical = "unknown"
    if origin.identity_valid and evidence.historical_creation_id is not None:
        historical = "user"
    source = {
        Provenance.USER: "local",
        Provenance.BUNDLED: "package_bundle",
    }.get(provenance, "unknown")
    return SkillIdentification(
        name=name,
        historical_creation_origin=historical,
        installation_source=source,
        authorization_basis=origin.authorization_basis,
        ownership=(provenance or Provenance.USER).value,
        current_installation_id=None if evidence.current is None else evidence.current.operation_id,
        historical_creation_id=evidence.historical_creation_id,
        current_removal_id=evidence.current_removal_id,
        pending_operation_ids=pending,
        identity_valid=origin.identity_valid,
        known_protection=(),
        protection_coverage="namespace_and_recorded_only",
        removivel=provenance is not None and not pending,
        reasons=origin.reasons + (("pending_operation",) if pending else ()),
    )
