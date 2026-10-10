"""Criação consome o arquivo real e preserva autoria explícita."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor, Provenance
from kairos_skills.catalog import catalog_home_id
from kairos_skills.frontmatter import parse_frontmatter
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutations import SkillMutationService
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class MutationServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "SKILL.md"
        self.source.write_bytes(creation_text().encode())
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.service = SkillMutationService(self.home, self.repo)

    def test_add_publica_registra_e_preserva_source(self):
        record = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        installed = self.home / "skills" / record.name / "SKILL.md"
        self.assertEqual(installed.read_bytes(), self.source.read_bytes())
        self.assertEqual(record.state, SkillMutationState.COMMITTED)
        self.assertEqual(record.provenance, Provenance.USER)
        self.assertEqual(
            self.repo.content(record.operation_id).text.encode(), self.source.read_bytes()
        )
        self.assertIsNone(parse_frontmatter(installed.read_text())[0].author)
        self.assertIs(self.service.ownership(record.name), Provenance.USER)
        self.assertIs(self.service.ownership("legacy"), Provenance.USER)
        self.assertEqual(self.service.history(), (record,))

    def test_existing_name_tombstone_invalid_no_effect(self):
        skills = self.home / "skills"
        skills.mkdir()
        target = skills / "revisar-docs"
        for kind in ("file", "empty", "link", "skill", "tombstone"):
            with self.subTest(kind=kind):
                if kind == "file":
                    target.write_text("preserved")
                elif kind == "empty":
                    target.mkdir()
                elif kind == "link":
                    target.symlink_to(self.root / "outside")
                elif kind == "skill":
                    target.mkdir()
                    (target / "SKILL.md").write_text(creation_text(body="existing"))
                else:
                    (skills / ".bundled_manifest").write_text("revisar-docs\n")
                with self.assertRaises(SkillMutationError):
                    self.service.add(self.source, actor=Actor.USER_FOREGROUND)
                self.assertEqual(self.repo.history(), ())
                if kind in ("file", "link"):
                    target.unlink()
                elif kind in ("empty", "skill"):
                    if kind == "skill":
                        self.assertIn("existing", (target / "SKILL.md").read_text())
                        (target / "SKILL.md").unlink()
                    target.rmdir()
                else:
                    (skills / ".bundled_manifest").unlink()
        for actor in (Actor.CURATOR, Actor.BACKGROUND_REVIEW, "user_foreground"):
            with self.subTest(actor=actor), self.assertRaises(SkillMutationError):
                self.service.add(self.source, actor=actor)
        self.source.write_text(creation_text(version="antiga"))
        with self.assertRaises(SkillMutationError):
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertFalse(target.exists())
        self.assertEqual(self.repo.history(), ())

    def test_content_metadata_nao_escolhe_origem(self):
        self.source.write_text(
            creation_text().replace(
                "---\nProcedimento",
                "origin: sediment\nmetadata:\n  kairos:\n    origin: background_review\n---\nProcedimento",
            )
        )
        with patch.dict(os.environ, {"KAIROS_ORIGIN": "sediment", "USER": "fake-curator"}):
            record = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertIs(record.provenance, Provenance.USER)
        self.assertIsNone(parse_frontmatter(self.repo.content(record.operation_id).text)[0].author)

    def test_constructor_is_readonly_and_other_home_refused(self):
        before = set(self.home.iterdir())
        SkillMutationService(self.home, self.repo)
        self.assertEqual(set(self.home.iterdir()), before)
        other = SkillMutationRepository(self.db, home_id="0" * 64)
        with self.assertRaises(SkillMutationError):
            SkillMutationService(self.home, other)

    def test_before_prepare_failure_cleans_only_known_staging(self):
        with (
            patch.object(self.repo, "prepare", side_effect=SkillMutationError("io", "fixture")),
            self.assertRaises(SkillMutationError),
        ):
            self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())
        self.assertEqual(list((self.home / ".skill-mutations" / "staging").iterdir()), [])
        self.assertEqual(self.repo.history(), ())
