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
from kairos_skills.adoption_contract import SkillAdoptionAction, SkillAdoptionRecord
from kairos_skills.identification_contract import InstallationEvidence, SkillIdentification
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState, validate_name
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
            root = captured.entries[0]
            try:
                current = (Path(home) / "skills" / name).lstat()
            except FileNotFoundError:
                return True
            except OSError:
                return False
            return (current.st_dev, current.st_ino) != (root.device, root.inode)
        raise
    return observed != captured


def _read_adoption_record(repository, adoption_id):
    return repository.get(adoption_id)


def _adoption_record_for_identification(*, home, name, evidence, database, captured):
    adoption = evidence.adoption
    if (
        evidence.current is None
        or adoption is None
        or adoption.installation_id != evidence.current.operation_id
    ):
        return None, None
    with io_errors():
        try:
            with database_connection(home, write=False) as connection:
                if connection is None:
                    raise SkillMutationError("corrupt", "Linhagem de adoção ausente.")
                if _database_fingerprint(connection) != database:
                    raise _changed()
                repository = read_skill_repository(connection, home)
                if repository is None:
                    raise SkillMutationError("corrupt", "Linhagem de adoção ausente.")
                record = _read_adoption_record(repository, adoption.adoption_id)
        except sqlite3.DatabaseError:
            raise SkillMutationError("io", "Não foi possível ler o ledger de skills.") from None
    if (
        not isinstance(record, SkillAdoptionRecord)
        or record.action is not SkillAdoptionAction.ADOPT
        or record.state is not SkillMutationState.COMMITTED
        or record.name != name
        or record.operation_id != adoption.adoption_id
        or record.draft.adoption_id != adoption.adoption_id
    ):
        raise SkillMutationError("corrupt", "Linhagem de adoção incoerente.")
    # The founder's installation ID stays immutable. A committed RESTORE may
    # transport its authority to a descendant installation; repository.get()
    # has already validated that committed lineage and its current projection.
    # The founder read is part of this observation. Revalidate the same
    # database and descriptor-captured root after resolving it.
    try:
        repeated, _ = _installation_evidence(home, name, expected_database=database)
        recaptured = read_installation_capture(home, name)
    except SkillMutationError as error:
        if error.kind == "io" and _installation_changed_after_io(home, name, captured):
            raise _changed() from None
        raise
    if repeated != evidence or recaptured != captured:
        raise _changed()
    return adoption, record


def _make_identification(name, evidence, origin, adoption, adoption_record):
    provenance = origin.provenance
    pending = evidence.pending_operation_ids
    historical = (
        "user"
        if origin.identity_valid and evidence.historical_creation_id is not None
        else "unknown"
    )
    source = (
        adoption_record.draft.installation_source
        if adoption_record is not None
        else {
            Provenance.USER: "local",
            Provenance.BUNDLED: "package_bundle",
        }.get(provenance, "unknown")
    )
    adoption_active = (
        None if adoption is None else adoption.active and origin.identity_valid is True
    )
    known_protection = (
        ("sediment",) if provenance is Provenance.SEDIMENT and origin.identity_valid else ()
    )
    if adoption is not None:
        authorization_basis = "user_adoption" if adoption_active else "none"
    elif known_protection:
        authorization_basis = "none"
    else:
        authorization_basis = origin.authorization_basis
    reasons = origin.reasons + (("pending_operation",) if pending else ())
    if adoption is not None and origin.identity_valid and not adoption.active:
        reasons += ("adoption_revoked",)
    if known_protection:
        reasons += ("sediment_protection",)
    return SkillIdentification(
        name=name,
        historical_creation_origin=historical,
        installation_source=source,
        authorization_basis=authorization_basis,
        ownership=(provenance or Provenance.USER).value,
        current_installation_id=None if evidence.current is None else evidence.current.operation_id,
        historical_creation_id=evidence.historical_creation_id,
        current_removal_id=evidence.current_removal_id,
        pending_operation_ids=pending,
        identity_valid=origin.identity_valid,
        known_protection=known_protection,
        protection_coverage="namespace_and_recorded_only",
        removivel=authorization_basis in {"proven_write", "verified_bundle", "user_adoption"}
        and not pending,
        reasons=reasons,
        adoption_id=None if adoption is None else adoption.adoption_id,
        adoption_active=adoption_active,
    )


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

    adoption, adoption_record = _adoption_record_for_identification(
        home=home,
        name=name,
        evidence=evidence,
        database=database,
        captured=captured,
    )
    return _make_identification(name, evidence, origin, adoption, adoption_record)
