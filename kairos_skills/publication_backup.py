"""Inventário verificável de backup e diagnóstico sem transferência de autoridade."""

from __future__ import annotations

import hashlib
import os
import stat
import tarfile
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from kairos_filesystem.contract import TreeCapture
from kairos_filesystem.descriptors import check_chain, open_directory
from kairos_filesystem.tree import capture_tree
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationError,
    SkillMutationState,
    require,
    validate_hash,
    validate_id,
    validate_name,
)
from kairos_skills.org_policy_guard import attest_policy_home
from kairos_skills.publication_contract import SkillPublicationRecord, publication_inventory_hash
from kairos_skills.publication_io import publication_errors
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeRecord, validate_capture
from kairos_skills.removal_io import LIMITS
from kairos_skills.removal_state import database_connection
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_publications import SkillPublicationRepository


@dataclass(frozen=True)
class PublicationBackupEntry:
    relative_path: str
    proof: TreeCapture

    def __post_init__(self):
        require(type(self.relative_path) is str)
        parts = PurePosixPath(self.relative_path).parts
        if len(parts) == 2 and parts[0] == "skills":
            validate_name(parts[1])
        elif len(parts) == 3 and parts[:2] == (".skill-mutations", "retired"):
            validate_id(parts[2])
        else:
            require(
                len(parts) == 3
                and parts[0] == ".skill-publications"
                and parts[2] in {"staged", "previous"}
            )
            validate_id(parts[1])
        require(PurePosixPath(*parts).as_posix() == self.relative_path)
        validate_capture(self.proof)
        require(self.proof.contents is None)


@dataclass(frozen=True)
class RestorationObservation:
    operation_id: str
    observed_home_id: str
    present_artifacts: tuple[str, ...]
    inventory_valid: bool
    identity_valid: bool
    reasons: tuple[str, ...]

    def __post_init__(self):
        validate_id(self.operation_id)
        validate_hash(self.observed_home_id)
        require(
            type(self.present_artifacts) is tuple
            and set(self.present_artifacts) <= {"installed", "staged", "previous"}
        )
        require(type(self.inventory_valid) is bool and type(self.identity_valid) is bool)
        require(type(self.reasons) is tuple and all(type(reason) is str for reason in self.reasons))


def validate_publication_archive(archive, entries):
    """A prova na origem também deve corresponder aos bytes e modos no tar."""
    with tarfile.open(archive) as tar:
        members = {}
        for member in tar.getmembers():
            members.setdefault(member.name.rstrip("/"), []).append(member)
        for entry in entries:
            expected = {
                entry.relative_path + ("/" + item.path if item.path else ""): item
                for item in entry.proof.entries
            }
            actual = {
                name
                for name in members
                if name == entry.relative_path or name.startswith(entry.relative_path + "/")
            }
            _archive_expect(actual == set(expected))
            for name, item in expected.items():
                _archive_expect(len(members[name]) == 1)
                member = members[name][0]
                _archive_expect(member.mode & 0o7777 == item.mode)
                if item.kind == "directory":
                    _archive_expect(member.isdir())
                else:
                    _archive_expect(member.isfile() and member.size == item.size)
                    with tar.extractfile(member) as content:
                        digest = hashlib.sha256()
                        while chunk := content.read(65536):
                            digest.update(chunk)
                    _archive_expect(digest.hexdigest() == item.sha256)


def _archive_expect(condition):
    if not condition:
        raise SkillMutationError(
            "conflict", "Arquivo de backup não conserva o inventário de publicação."
        )


def _capture(path):
    with publication_errors(), ExitStack() as stack:
        try:
            parent, chain = open_directory(stack, path.parent)
        except FileNotFoundError:
            return None
        try:
            os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            check_chain(chain)
            return None
        result = capture_tree(parent, path.name, limits=LIMITS, include_contents=False)
        validate_capture(result)
        check_chain(chain)
        return result


def _paths(home, record):
    area = home / ".skill-publications" / record.operation_id
    return {
        "installed": home / "skills" / record.name,
        "staged": area / "staged",
        "previous": area / "previous",
    }


def _private_area(home, record):
    with publication_errors(), ExitStack() as stack:
        for path in (
            home / ".skill-publications",
            home / ".skill-publications" / record.operation_id,
        ):
            fd, chain = open_directory(stack, path)
            require(
                stat.S_IMODE(os.fstat(fd).st_mode) == 0o700, "Área privada de publicação alterada."
            )
            check_chain(chain)


def _refuse(record):
    raise SkillMutationError(
        "conflict",
        "Artefato de publicação ausente ou divergente; backup anterior preservado.",
        operation_id=record.operation_id,
    )


def _pending_layout(record, observed, *, inventory_only=False):
    expected_new, expected_old = record.draft.staged_tree, record.draft.previous_tree

    def matches(actual, expected):
        if actual is None or expected is None:
            return actual is expected
        return (
            publication_inventory_hash(actual) == publication_inventory_hash(expected)
            if inventory_only
            else actual == expected
        )

    installed, staged, previous = (observed[key] for key in ("installed", "staged", "previous"))
    if record.state is SkillMutationState.ABORTED:
        return staged is None and previous is None and matches(installed, expected_old)
    return (
        matches(installed, expected_new) and staged is None and matches(previous, expected_old)
    ) or (
        matches(staged, expected_new)
        and (
            (matches(installed, expected_old) and previous is None)
            or (installed is None and matches(previous, expected_old))
        )
    )


def _root_identity(proof):
    root = proof.entries[0]
    return root.device, root.inode, root.mount_id


def _relocated_installation(home, record, common):
    expected = _root_identity(record.draft.staged_tree)
    candidates = []
    for item in common.pending(record.name):
        if isinstance(item, SkillTreeRecord) and item.action is SkillTreeAction.REMOVE:
            proof = item.draft.proof
            relative = f".skill-mutations/retired/{item.operation_id}"
        elif (
            isinstance(item, SkillPublicationRecord)
            and item.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT)
            and item.draft.previous_tree is not None
        ):
            proof = item.draft.previous_tree
            relative = f".skill-publications/{item.operation_id}/previous"
        else:
            continue
        if _root_identity(proof) == expected:
            candidates.append((relative, proof))
    if len(candidates) != 1:
        _refuse(record)
    relative, expected_proof = candidates[0]
    path = home / relative
    with publication_errors(), ExitStack() as stack:
        for directory in (path.parent.parent, path.parent):
            fd, chain = open_directory(stack, directory)
            if stat.S_IMODE(os.fstat(fd).st_mode) != 0o700:
                _refuse(record)
            check_chain(chain)
    proof = _capture(path)
    if proof != expected_proof:
        _refuse(record)
    return PublicationBackupEntry(relative, proof)


def _record_inventory(home, record, current, common):
    _private_area(home, record)
    paths = _paths(home, record)
    observed = {key: _capture(path) for key, path in paths.items() if key != "installed"}
    if record.state is SkillMutationState.ABORTED:
        return ()
    if record.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
        observed["installed"] = _capture(paths["installed"])
        if not _pending_layout(record, observed):
            _refuse(record)
    else:
        if observed["staged"] is not None or observed["previous"] != record.draft.previous_tree:
            _refuse(record)
        if current is not None and current.operation_id == record.operation_id:
            installed = _capture(paths["installed"])
            if installed is None or _root_identity(installed) != _root_identity(
                record.draft.staged_tree
            ):
                relocated = _relocated_installation(home, record, common)
                return (
                    relocated,
                    *(
                        PublicationBackupEntry(str(paths[key].relative_to(home)), proof)
                        for key, proof in observed.items()
                        if proof is not None
                    ),
                )
            root = installed.entries[0]
            expected = record.draft.staged_tree.entries[0]
            if (root.device, root.inode, root.mount_id) != (
                expected.device,
                expected.inode,
                expected.mount_id,
            ):
                _refuse(record)
            observed["installed"] = installed
    return tuple(
        PublicationBackupEntry(str(paths[key].relative_to(home)), proof)
        for key, proof in observed.items()
        if proof is not None
    )


def publication_backup_inventory(
    home: Path, repository: SkillPublicationRepository
) -> tuple[PublicationBackupEntry, ...]:
    home = Path(home).absolute()
    require(
        type(repository) is SkillPublicationRepository
        and repository.home_id == catalog_home_id(home)
    )
    common = SkillMutationRepository(repository._conn, home_id=repository.home_id)
    result = {}
    records = repository.history()
    foreign = repository._conn.execute(
        "SELECT operation_id FROM skill_publication_operations WHERE home_id<>? ORDER BY sequence LIMIT 1",
        (repository.home_id,),
    ).fetchone()
    if foreign is not None:
        raise SkillMutationError(
            "conflict",
            "Histórico de outro home contém referências que este backup integral não pode ignorar.",
            operation_id=foreign[0],
        )
    for record in records:
        for entry in _record_inventory(
            home, record, common.current_installation(record.name), common
        ):
            result[entry.relative_path] = entry
    return tuple(result[name] for name in sorted(result))


def validate_restored_publication(
    home: Path, record: SkillPublicationRecord, *, policy
) -> RestorationObservation:
    require(type(record) is SkillPublicationRecord)
    home = Path(home).absolute()
    attest_policy_home(policy, home)
    reasons = []
    try:
        _private_area(home, record)
    except SkillMutationError:
        reasons.append("private_area_unverifiable")
    observed = {}
    for key, path in _paths(home, record).items():
        try:
            observed[key] = _capture(path)
        except SkillMutationError:
            observed[key] = None
            reasons.append("unverifiable_" + key)
    with database_connection(home) as connection:
        canonical = (
            None
            if connection is None
            else SkillPublicationRepository(connection, home_id=record.home_id).get(
                record.operation_id
            )
        )
    if canonical != record:
        reasons.append("canonical_record_mismatch")
    if record.state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
        inventory = _pending_layout(record, observed, inventory_only=True)
        identity = _pending_layout(record, observed)
    else:
        inventory = (
            _pending_layout(record, observed, inventory_only=True) and observed["staged"] is None
        )
        identity = _pending_layout(record, observed) and observed["staged"] is None
    if not inventory:
        reasons.append("missing_or_changed_inventory")
    if not identity:
        reasons.append("artifact_identity_mismatch")
    if catalog_home_id(home) != record.home_id:
        identity = False
        reasons.append("different_home")
    if not record.cleanup_complete:
        identity = False
        reasons.append("external_source_requires_live_context")
    policy.revalidate()
    return RestorationObservation(
        record.operation_id,
        catalog_home_id(home),
        tuple(key for key, proof in observed.items() if proof is not None),
        inventory
        and canonical == record
        and not any(
            reason.startswith("unverifiable_") or reason == "private_area_unverifiable"
            for reason in reasons
        ),
        identity and canonical == record and "private_area_unverifiable" not in reasons,
        tuple(reasons),
    )
