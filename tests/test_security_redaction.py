"""Regressões do detector real com segredos exclusivamente sintéticos."""

import argparse
import dataclasses
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from kairos_security import SecurityReport, Severity, format_cli_summary, format_json_report
from kairos_security.checks.secrets import check_hardcoded_secrets


class SecretDetectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kairos-secrets-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _scan(self, filename, content):
        path = self.root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return check_hardcoded_secrets(self.root)

    def test_detects_env_names_without_relying_on_suffix(self):
        secret = "sk-ant-" + "Q7w_" * 12
        for filename in (".env", ".env.local", ".env.production", ".env.production.local"):
            with self.subTest(filename=filename):
                findings = self._scan(filename, "TOKEN=" + secret)
                self.assertTrue(any(f.file_path == str(self.root / filename) for f in findings))
                (self.root / filename).unlink()

    def test_preserves_detection_for_supported_extensions(self):
        secret = "sk-ant-" + "Q7w_" * 12
        for filename in ("settings.py", "settings.json", "service.env"):
            with self.subTest(filename=filename):
                findings = self._scan(filename, "TOKEN=" + secret)
                self.assertTrue(any(f.file_path == str(self.root / filename) for f in findings))
                (self.root / filename).unlink()

    def test_detects_legacy_and_scoped_openai_formats(self):
        keys = (
            "sk-" + "A7" * 10 + "T3BlbkFJ" + "B9" * 10,
            "sk-proj-" + "Q7w_" * 18 + "A2" + "T3BlbkFJ" + "R9s-" * 18 + "B3",
            "sk-svcacct-" + "Q7w_" * 18 + "A2" + "T3BlbkFJ" + "R9s-" * 18 + "B3",
            "sk-admin-" + "Q7w_" * 14 + "A2" + "T3BlbkFJ" + "R9s-" * 14 + "B3",
        )
        for key in keys:
            with self.subTest(prefix=key.split("-")[1] if key.count("-") > 1 else "legacy"):
                findings = self._scan("settings.py", "\nTOKEN=" + key + "\n")
                self.assertTrue(any("OpenAI" in f.title for f in findings))
                self.assertTrue(all(f.severity == Severity.CRITICAL for f in findings))
                self.assertTrue(all(f.line_number == 2 for f in findings))

    def test_does_not_label_anthropic_or_malformed_tokens_as_openai(self):
        keys = (
            "sk-ant-" + "Q7w_" * 12,
            "sk-" + "A7" * 24,
            "sk-proj-short",
            "sk-" + "A7" * 10 + "T3BlbkFJ" + "B9",
        )
        for key in keys:
            with self.subTest(prefix=key[:12]):
                findings = self._scan("settings.py", "TOKEN=" + key)
                self.assertFalse(any("OpenAI" in f.title for f in findings))

    def test_preserves_cofre_and_ignored_directory_exclusions(self):
        secret = "sk-ant-" + "Q7w_" * 12
        for filename in ("auth.json", ".git/.env", ".venv/.env.local", "node_modules/.env"):
            with self.subTest(filename=filename):
                findings = self._scan(filename, "TOKEN=" + secret)
                self.assertFalse(any(f.file_path == str(self.root / filename) for f in findings))

    def test_does_not_expand_to_unrelated_dotfiles(self):
        secret = "sk-ant-" + "Q7w_" * 12
        findings = self._scan(".envrc", "TOKEN=" + secret)
        self.assertFalse(findings)

    def test_evidence_and_serialized_outputs_omit_secret_and_line_context(self):
        secrets = (
            "sk-" + "A7" * 10 + "T3BlbkFJ" + "B9" * 10,
            "sk-proj-" + "Q7w_" * 18 + "A2" + "T3BlbkFJ" + "R9s-" * 18 + "B3",
            "sk-ant-" + "Q7w_" * 12,
            "AIzaSy" + "A7q_" * 8 + "B",
            "Bearer " + "B9r_" * 10,
        )
        for secret in secrets:
            with self.subTest(prefix=secret[:12]):
                context = "PRIVATE_LINE_CONTEXT"
                findings = self._scan("settings.py", context + "=" + secret)
                self.assertTrue(findings)
                report = SecurityReport(target=str(self.root), findings=findings)
                outputs = (
                    format_json_report(report),
                    format_cli_summary(report),
                    json.dumps([dataclasses.asdict(f) for f in findings]),
                    repr(findings),
                )
                for finding in findings:
                    self.assertTrue(finding.evidence)
                for output in outputs:
                    self.assertNotIn(context, output)
                    self.assertNotIn(secret, output)
                    self.assertNotIn(secret[:16], output)
                    self.assertNotIn(secret[-16:], output)

    def test_evidence_is_independent_of_all_secrets_and_context_on_same_line(self):
        first = self._scan("settings.py", "PRIVATE_A=sk-ant-" + "Q7w_" * 12)
        second = self._scan(
            "settings.py", "PRIVATE_B=sk-ant-" + "R9s_" * 12 + "; OTHER=sk-ant-" + "M6n_" * 12
        )
        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(first, second)

    def test_cli_audits_only_synthetic_target_and_returns_redacted_findings(self):
        from kairos_cli.handlers import cmd_security

        secret = "sk-ant-" + "Q7w_" * 12
        (self.root / ".env").write_text("PRIVATE_CLI_CONTEXT=" + secret, encoding="utf-8")
        for as_json in (True, False):
            with self.subTest(as_json=as_json):
                args = argparse.Namespace(
                    target=str(self.root), source=None, no_source=True, json=as_json, fail_on="high"
                )
                output = io.StringIO()
                with redirect_stdout(output):
                    status = cmd_security(args)
                rendered = output.getvalue()
                self.assertNotEqual(status, 0)
                self.assertNotIn(secret, rendered)
                self.assertNotIn(secret[:16], rendered)
                self.assertNotIn("PRIVATE_CLI_CONTEXT", rendered)
                if as_json:
                    findings = json.loads(rendered)["findings"]
                    self.assertTrue(any(f["id"] == "SEC-SECRET-001" for f in findings))
                else:
                    self.assertIn("SEC-SECRET-001", rendered)
