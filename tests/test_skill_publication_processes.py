"""Interrupções não capturáveis e clientes instalados anteriores."""

import hashlib
import json
import os
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fixtures.skill_publication_process import run_divergences, run_matrix


def canonical(connection):
    from kairos_state.migrations import CANONICAL_TABLES

    tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    return {
        name: tuple(tuple(row) for row in connection.execute('SELECT * FROM "' + name + '"'))  # noqa: S608 — nomes internos de sqlite_master.
        for name in sorted(tables & CANONICAL_TABLES)
    }


def installed(home):
    return [
        (str(p.relative_to(home)), p.stat().st_ino, p.read_bytes() if p.is_file() else None)
        for p in sorted((home / "skills").rglob("*"))
    ]


class PublicationProcessTests(unittest.TestCase):
    def test_v10_client_refuses_v11_after_repair(self):
        from kairos_state import connect
        from kairos_state.migrations import migrate, repair_derived_objects

        python = self.old_client()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for action in ("add", "remove", "rollback", "restore", "adopt", "unadopt"):
                for upgraded in (False, True):
                    with self.subTest(action=action, upgraded=upgraded):
                        home = root / (action + str(upgraded))
                        home.mkdir(mode=0o700)
                        source = root / "source.md"
                        source.write_text(
                            "---\nname: personal-skill\ndescription: Teste.\nversion: '0.1.0'\n---\nTeste.\n"
                        )

                        def run(*arguments, home=home):
                            return subprocess.run(
                                [
                                    python,
                                    "-I",
                                    "-c",
                                    "import sys; from kairos_cli.main import main; sys.exit(main())",
                                    "skills",
                                    *arguments,
                                    "--json",
                                ],
                                cwd=root,
                                env=os.environ | {"KAIROS_HOME": str(home)},
                                capture_output=True,
                                text=True,
                                check=False,
                            )

                        created = run("add", "--file", str(source))
                        self.assertEqual(created.returncode, 0, created.stderr)
                        creation_id = json.loads(created.stdout)["id"]
                        command = ("remove", "personal-skill", "--yes")
                        if action == "add":
                            source.write_text(
                                source.read_text().replace("personal-skill", "another-skill")
                            )
                            command = ("add", "--file", str(source))
                        elif action == "rollback":
                            command = ("rollback", creation_id)
                        elif action == "restore":
                            removed = run(*command)
                            self.assertEqual(removed.returncode, 0, removed.stderr)
                            command = ("rollback", json.loads(removed.stdout)["id"])
                        elif action in ("adopt", "unadopt"):
                            target = home / "skills/external-skill"
                            target.mkdir()
                            (target / "SKILL.md").write_text(
                                source.read_text().replace("personal-skill", "external-skill")
                            )
                            command = ("adopt", "external-skill", "--yes")
                            if action == "unadopt":
                                adopted = run(*command)
                                self.assertEqual(adopted.returncode, 0, adopted.stderr)
                                command = ("unadopt", "external-skill", "--yes")
                        with closing(connect(home / "state.db")) as connection:
                            if upgraded:
                                migrate(connection)
                                repair_derived_objects(connection)
                            before = canonical(connection), installed(home)
                            result = run(*command)
                            if upgraded:
                                self.assertNotEqual(result.returncode, 0, result.stdout)
                                self.assertRegex(
                                    result.stderr, "incompatível|segurança|não suportado|incoerente"
                                )
                                self.assertEqual((canonical(connection), installed(home)), before)
                            else:
                                self.assertEqual(result.returncode, 0, result.stderr)

    def old_client(self):
        provenance_path = Path(
            os.environ.get(
                "KAIROS_SKILL_V10_PROVENANCE",
                ".superpowers/test-fixtures/skill-client-v10/provenance.json",
            )
        ).resolve()
        self.assertTrue(provenance_path.is_file(), "Execute scripts/prepare-skill-v10-client.sh")
        provenance = json.loads(provenance_path.read_text())
        self.assertEqual(provenance["revision"], "44d890ca632b40b78a21b62c5e05622200a5b23c")
        self.assertEqual(
            hashlib.sha256(Path(provenance["wheel"]).read_bytes()).hexdigest(), provenance["sha256"]
        )
        python = provenance["python"]
        probe = subprocess.run(
            [
                python,
                "-I",
                "-c",
                "import json,kairos_state.schema as s; print(json.dumps([s.__file__,s.SCHEMA_VERSION]))",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        module, version = json.loads(probe.stdout)
        self.assertEqual(version, 10)
        self.assertTrue(Path(module).is_relative_to(Path(python).parent.parent / "lib"))
        return python

    def test_capabilities_8_9_10_refuse_every_family_after_repair(self):
        from kairos_state import connect, initialize_schema
        from kairos_state.migrations import repair_derived_objects

        with (
            tempfile.TemporaryDirectory() as directory,
            closing(connect(Path(directory) / "state.db")) as connection,
        ):
            initialize_schema(connection)
            repair_derived_objects(connection)
            before = canonical(connection)
            for capability in (None, 8, 9, 10):
                if capability is not None:
                    connection.create_function(
                        "kairos_skill_writer_version", 0, lambda value=capability: value
                    )
                else:
                    connection.create_function("kairos_skill_writer_version", 0, lambda: None)
                for table in (
                    "skill_mutation_operations",
                    "skill_mutation_events",
                    "skill_tree_operations",
                    "skill_tree_events",
                    "skill_adoption_operations",
                    "skill_adoption_events",
                    "skill_org_intents",
                    "skill_acquisition_receipts",
                    "skill_publication_operations",
                    "skill_publication_events",
                ):
                    with self.subTest(capability=capability, table=table):
                        with (
                            self.assertRaisesRegex(
                                sqlite3.DatabaseError, "unsupported skill writer"
                            ),
                            connection,
                        ):
                            connection.execute(f"INSERT INTO {table} DEFAULT VALUES")
                        self.assertEqual(canonical(connection), before)

    def test_default_release_exposes_publications_in_new_and_upgraded_home(self):
        from kairos_state import connect, initialize_schema
        from kairos_state.migrations import migrate
        from kairos_state.repositories.skill_publications import SkillPublicationRepository

        with tempfile.TemporaryDirectory() as directory:
            for old in (False, True):
                with self.subTest(old=old):
                    connection = connect(Path(directory) / str(old) / "state.db")
                    try:
                        if old:
                            migrate(connection, target=10)
                        initialize_schema(connection)
                        self.assertEqual(
                            SkillPublicationRepository(connection, home_id="a" * 64).history(), ()
                        )
                    finally:
                        connection.close()

    def test_sigkill_boundaries_preserve_recoverable_versions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = run_matrix(root, root)
            self.assertGreater(result["sigkills"], 40)

    def test_policy_missing_source_and_identical_root_replacement_between_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(run_divergences(root, root)["divergences"], 3)
