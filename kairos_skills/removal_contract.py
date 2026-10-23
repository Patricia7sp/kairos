"""Contratos de remoção e restauração de árvores de skills."""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Union

from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture, TreeEntry
from kairos_skills.adoption_contract import (
    INSTALLATION_SOURCES,
    AdoptionAuthority,
    SkillAdoptionRecord,
)
from kairos_skills.mutation_contract import (
    SkillMutationRecord,
    SkillMutationState,
    require,
    validate_actor,
    validate_hash,
    validate_id,
    validate_name,
)

if TYPE_CHECKING:
    from kairos_skills.publication_contract import SkillPublicationRecord

MAX_TREE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TREE_ENTRIES = 4096
MAX_TREE_DEPTH = 32
MAX_COMPONENT_BYTES = 255
# Cada byte do nome POSIX pode ocupar seis bytes escapados no JSON ASCII.
# A margem por entrada cobre chaves, separadores, hashes e identidades uint64.
MAX_MANIFEST_BYTES = MAX_TREE_ENTRIES * (MAX_TREE_DEPTH * (MAX_COMPONENT_BYTES * 6 + 1) + 512)


def validate_capture(capture: TreeCapture, *, contents: bool = False) -> None:
    """Valida o inventário completo antes de consumir qualquer conteúdo."""
    require(type(capture) is TreeCapture and type(capture.entries) is tuple)
    require(1 <= len(capture.entries) <= MAX_TREE_ENTRIES)
    paths: dict[str, TreeEntry] = {}
    total = 0
    for entry in capture.entries:
        require(type(entry) is TreeEntry and type(entry.path) is str)
        require("\x00" not in entry.path)
        parts = entry.path.split("/") if entry.path else []
        require(len(parts) <= MAX_TREE_DEPTH and all(part not in ("", ".", "..") for part in parts))
        require(all(len(os.fsencode(part)) <= MAX_COMPONENT_BYTES for part in parts))
        require(entry.path not in paths)
        require(type(entry.kind) is str and entry.kind in ("directory", "file"))
        require(type(entry.mode) is int and 0 <= entry.mode <= 0o777)
        require(type(entry.size) is int and entry.size >= 0)
        for value in (entry.device, entry.inode, entry.mount_id):
            require(type(value) is int and 0 <= value < 2**64)
        require(entry.inode > 0 and entry.mount_id > 0)
        if entry.kind == "directory":
            require(entry.size == 0 and entry.sha256 is None)
        else:
            validate_hash(entry.sha256)
            require(entry.size <= (65536 if entry.path == "SKILL.md" else MAX_FILE_BYTES))
            total += entry.size
            require(total <= MAX_TREE_BYTES)
        paths[entry.path] = entry
    require(tuple(paths) == tuple(sorted(paths)))
    require("" in paths and paths[""].kind == "directory")
    require("SKILL.md" in paths and paths["SKILL.md"].kind == "file")
    root = paths[""]
    for path, entry in paths.items():
        require((entry.device, entry.mount_id) == (root.device, root.mount_id))
        if path:
            parent = path.rpartition("/")[0]
            require(parent in paths and paths[parent].kind == "directory")
    if contents or capture.contents is not None:
        require(type(capture.contents) is dict)
        require(
            set(capture.contents) == {path for path, entry in paths.items() if entry.kind == "file"}
        )
        for path, data in capture.contents.items():
            require(type(data) is bytes and len(data) == paths[path].size)
            require(hashlib.sha256(data).hexdigest() == paths[path].sha256)


class SkillTreeAction(StrEnum):
    REMOVE = "remove"
    RESTORE = "restore"


@dataclass(frozen=True)
class SkillTreeDraft:
    operation_id: str
    home_id: str
    action: SkillTreeAction
    name: str
    actor: Actor
    created_at: float
    provenance: Provenance
    proof: TreeCapture
    snapshot_id: str
    reverts: str | None = None
    adoption: AdoptionAuthority | None = None
    installation_source: str | None = None

    def __post_init__(self):
        validate_actor(self.actor)
        validate_id(self.operation_id)
        validate_hash(self.home_id)
        validate_name(self.name)
        validate_id(self.snapshot_id)
        require(type(self.action) is SkillTreeAction)
        require(
            type(self.created_at) is float
            and math.isfinite(self.created_at)
            and self.created_at >= 0
        )
        require(
            type(self.provenance) is Provenance
            and self.provenance in (Provenance.USER, Provenance.BUNDLED)
        )
        validate_capture(self.proof)
        if self.adoption is None:
            require(self.installation_source is None)
        else:
            require(type(self.adoption) is AdoptionAuthority and self.adoption.active)
            require(
                self.provenance is Provenance.USER
                and self.installation_source in INSTALLATION_SOURCES
            )
            if self.action is SkillTreeAction.RESTORE:
                require(self.adoption.installation_id == self.operation_id)
        if self.action is SkillTreeAction.REMOVE:
            require(self.snapshot_id == self.operation_id and self.reverts is None)
        else:
            validate_id(self.reverts)
            require(self.snapshot_id == self.reverts and self.reverts != self.operation_id)


@dataclass(frozen=True)
class SkillTreeRecord:
    draft: SkillTreeDraft
    state: SkillMutationState

    def __post_init__(self):
        require(type(self.draft) is SkillTreeDraft and type(self.state) is SkillMutationState)

    @property
    def operation_id(self):
        return self.draft.operation_id

    @property
    def name(self):
        return self.draft.name

    @property
    def action(self):
        return self.draft.action

    @property
    def actor(self):
        return self.draft.actor

    @property
    def provenance(self):
        return self.draft.provenance

    @property
    def reverts(self):
        return self.draft.reverts


@dataclass(frozen=True)
class InstallationProof:
    operation_id: str
    provenance: Provenance | None
    device: int
    inode: int
    adoption: AdoptionAuthority | None = None
    installation_source: str | None = None

    def __post_init__(self):
        validate_id(self.operation_id)
        require(
            (
                type(self.provenance) is Provenance
                and self.provenance in (Provenance.USER, Provenance.BUNDLED)
            )
            or (self.provenance is None and self.installation_source == "verified_acquisition")
        )
        require(self.installation_source in (None, "package_bundle", "verified_acquisition"))
        require(type(self.device) is int and 0 <= self.device < 2**64)
        require(type(self.inode) is int and 0 < self.inode < 2**64)
        if self.adoption is not None:
            require(type(self.adoption) is AdoptionAuthority)
            require(
                self.adoption.installation_id == self.operation_id
                and self.provenance is Provenance.USER
            )


CommonRecord = Union[
    SkillMutationRecord, SkillTreeRecord, SkillAdoptionRecord, "SkillPublicationRecord"
]
