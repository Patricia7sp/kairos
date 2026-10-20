"""Configuração inválida recusa antes da abertura mutante do estado."""

import argparse
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_cli.skill_adoptions import run_skill_adoption
from kairos_cli.skill_mutations import run_skill_mutation


class OrgPolicyCliTests(unittest.TestCase):
    def test_bad_policy_does_not_create_or_migrate_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "SKILL.md"
            source.write_text(creation_text())
            for value, expected in (
                ("", 2),
                ("relative.json", 2),
                (str(root / "private-source-sentinel.json"), 69),
            ):
                for command in ("add", "adopt"):
                    with self.subTest(value=value, command=command):
                        home = root / "absent-home"
                        args = argparse.Namespace(
                            skills_command=command,
                            file=str(source),
                            name="revisar-docs",
                            yes=True,
                            json=True,
                            expect_adoption=None,
                        )
                        output, error = io.StringIO(), io.StringIO()
                        handler = run_skill_mutation if command == "add" else run_skill_adoption
                        with (
                            patch.dict(os.environ, {"KAIROS_SKILL_ORG_POLICY": value}),
                            redirect_stdout(output),
                            redirect_stderr(error),
                        ):
                            result = handler(home, args)
                        self.assertEqual(result, expected)
                        self.assertEqual(output.getvalue(), "")
                        self.assertFalse(home.exists())
                        self.assertNotIn("private-source-sentinel", error.getvalue())
