"""Arquivos e SQLite reais para os contratos de publicação."""

import hashlib
import json
import os
import tempfile
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.org_policy_contract import OrgPolicyReceipt
from kairos_skills.removal_io import capture_skill
from kairos_state import connect
from kairos_state.migrations import migrate


def canonical_rows(connection, tables):
    return {
        name: tuple(tuple(row) for row in connection.execute('SELECT * FROM "' + name + '"'))  # noqa: S608 — nomes observados em sqlite_master, sem entrada externa
        for name in tables
    }


class PublicationStateFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.connection = connect(self.home / "state.db")
        self.addCleanup(self.connection.close)
        migrate(self.connection, target=10)
        self.home_id = catalog_home_id(self.home)

    def legacy_creation(self):
        from kairos_skills.mutation_contract import (
            SkillDirectoryIdentity,
            SkillMutationAction,
            SkillMutationDraft,
            SkillMutationState,
            validate_skill_creation,
        )
        from kairos_state.repositories.skill_mutations import SkillMutationRepository

        text = "---\nname: legacy\ndescription: Fixture.\nversion: '0.1.0'\n---\nLegacy.\n"
        capture = self.capture(self.home / "skills/legacy", text.encode())
        root, entry = capture.entries
        creation = validate_skill_creation(text)
        draft = SkillMutationDraft(
            uuid.uuid4().hex,
            self.home_id,
            SkillMutationAction.CREATE,
            "legacy",
            Actor.USER_FOREGROUND,
            1.0,
            SkillDirectoryIdentity(root.device, root.inode, entry.device, entry.inode),
            creation.sha256,
            creation.size_bytes,
        )
        repository = SkillMutationRepository(self.connection, home_id=self.home_id)
        repository.prepare(
            draft,
            text,
            policy_receipt=OrgPolicyReceipt(self.home_id, "legacy", uuid.uuid4().hex, None, ()),
        )
        repository.finish(draft.operation_id, SkillMutationState.COMMITTED)
        return draft

    def capture(self, path, text=b"publication"):
        path.mkdir(parents=True, mode=0o700)
        path.chmod(0o700)
        (path / "SKILL.md").write_bytes(text)
        (path / "SKILL.md").chmod(0o600)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            return replace(capture_skill(parent, path.name), contents=None)
        finally:
            os.close(parent)

    def upgrade(self):
        migrate(self.connection, target=11)
        from kairos_state.repositories.skill_publications import SkillPublicationRepository

        return SkillPublicationRepository(self.connection, home_id=self.home_id)

    def publication(self, *, manifest=False):
        from kairos_skills.publication_contract import (
            AcquisitionReceipt,
            ManifestDelta,
            PublicationDraft,
            PublicationWriter,
            publication_inventory_hash,
        )

        acquisition_id, operation_id = uuid.uuid4().hex, uuid.uuid4().hex
        name = "personal-skill"
        source = self.capture(self.home / "quarantine" / acquisition_id / name)
        staged = self.capture(self.home / ".skill-publications" / operation_id / "staged")
        inventory = publication_inventory_hash(source)
        scan = hashlib.sha256(
            json.dumps(
                {"inventory_sha256": inventory, "findings": []},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        receipt = AcquisitionReceipt(
            1,
            acquisition_id,
            self.home_id,
            name,
            "kairos_quarantine_v1",
            acquisition_id,
            "unverified",
            inventory,
            scan,
            Actor.USER_FOREGROUND,
            source,
        )
        policy = OrgPolicyReceipt(self.home_id, name, uuid.uuid4().hex, None, ())
        draft = PublicationDraft(
            operation_id,
            self.home_id,
            name,
            f"{acquisition_id}/{name}",
            acquisition_id,
            PublicationWriter.QUARANTINE_PROMOTE,
            Actor.USER_FOREGROUND,
            source,
            staged,
            None,
            None,
            policy,
            ManifestDelta(name, None, "a" * 32) if manifest else None,
            1.0,
        )
        return receipt, draft
