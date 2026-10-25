"""Fixtures de memo com SQL, journal e árvores reais, sem dados pessoais."""

import tempfile
import unittest
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from skill_mutation_fixtures import creation_text
from skill_org_fixtures import personal_policy

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutations import SkillMutationService
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class RecordMemoFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="kairos-record-memo-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "SKILL.md"
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.service = SkillMutationService(
            self.home, self.repo, policy=personal_policy(self, self.home)
        )
        self.records = []
        for name in ("first", "second", "third"):
            self.source.write_text(creation_text(name=name))
            self.records.append(self.service.add(self.source, actor=Actor.USER_FOREGROUND))
        self.loads = Counter()

    @contextmanager
    def memo_scope(self, *, limit=1024, connection=None, home_id=None):
        from kairos_state.repositories.skill_record_memo import RecordMemo

        connection = self.db if connection is None else connection
        connection.execute("BEGIN IMMEDIATE")
        memo = RecordMemo(
            connection,
            self.repo.home_id if home_id is None else home_id,
            owner_token=object(),
            limit=limit,
        )
        try:
            yield memo
        finally:
            memo.close()
            connection.rollback()

    def load_record(self, memo, record, *, modern=True):
        def load():
            self.loads[record.operation_id] += 1
            return self.repo._get_validated(record.operation_id, modern=modern)

        return memo.load(record.operation_id, modern=modern, loader=load)
