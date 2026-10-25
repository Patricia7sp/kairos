"""A verificação fora dos tempos não modifica o namespace da conexão medida."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text
from skill_record_memo_fixtures import RecordMemoFixture

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import SkillMutationState

spec = importlib.util.spec_from_file_location(
    "writer_benchmark", Path(__file__).resolve().parents[1] / "scripts/benchmark-skill-writers.py"
)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkFixtureTests(RecordMemoFixture):
    def created_trees(self):
        created = []
        for record in self.records:
            directory = self.home / "skills" / record.name
            status = directory.stat()
            created.append(
                (record.name, (directory / "SKILL.md").read_text(), status.st_dev, status.st_ino)
            )
        return created

    def test_integrity_verification_preserves_namespace_and_subsequent_reuse(self):
        namespaces = tuple(self.db.execute("PRAGMA database_list"))
        verified = benchmark.verify_fixture(self.db, self.home, self.created_trees(), 11)
        self.assertEqual(verified["integrity"], "ok")
        self.assertEqual(tuple(self.db.execute("PRAGMA database_list")), namespaces)

        known = {record.operation_id for record in self.records}
        original = self.repo._legacy_get

        def counted(operation_id):
            if self.db.in_transaction and operation_id in known:
                self.loads[operation_id] += 1
            return original(operation_id)

        self.source.write_text(creation_text(name="after-verification"))
        with patch.object(self.repo, "_legacy_get", counted):
            result = self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.assertEqual(result.state, SkillMutationState.COMMITTED)
        self.assertEqual(dict(self.loads), dict.fromkeys(known, 1))

    def test_separate_inspection_still_refuses_real_integrity_violation(self):
        self.db.execute("CREATE TABLE integrity_probe(value INTEGER)")
        self.db.execute("INSERT INTO integrity_probe VALUES(NULL)")
        self.db.commit()
        self.db.execute("PRAGMA writable_schema=ON")
        self.db.execute(
            "UPDATE sqlite_schema SET sql='CREATE TABLE integrity_probe(value INTEGER NOT NULL)' "
            "WHERE name='integrity_probe'"
        )
        self.db.commit()
        self.db.execute("PRAGMA writable_schema=OFF")
        with self.assertRaises(RuntimeError):
            benchmark.verify_fixture(self.db, self.home, self.created_trees(), 11)
