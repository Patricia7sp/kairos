"""Testes unitários e de integração para a suíte de auditoria de segurança (kairos_security)."""

import os
import tempfile
import unittest
from pathlib import Path

from kairos_security import (
    Category,
    SecurityReport,
    Severity,
    format_cli_summary,
    format_json_report,
    run_full_audit,
)
from kairos_security.checks.path_traversal import check_path_traversal_guards
from kairos_security.checks.permissions import check_file_permissions


class SecurityAuditTests(unittest.TestCase):
    def test_permission_checker_flags_insecure_auth(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            auth_file = tmp_path / "auth.json"
            auth_file.write_text("{}", encoding="utf-8")

            if os.name != "nt":
                # Torna permissão insegura (0644)
                auth_file.chmod(0o644)
                findings = check_file_permissions(tmp_path)
                self.assertTrue(any(f.id == "SEC-PERM-001" for f in findings))

                # Corrige para 0600
                auth_file.chmod(0o600)
                clean_findings = check_file_permissions(tmp_path)
                self.assertFalse(any(f.id == "SEC-PERM-001" for f in clean_findings))

    def test_path_traversal_guards(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Dentro do root: seguro
            self.assertTrue(check_path_traversal_guards(root, "subdir/arquivo.txt"))
            # Tentativas de traversal fora do root: inseguro
            self.assertFalse(check_path_traversal_guards(root, "../../etc/passwd"))
            self.assertFalse(check_path_traversal_guards(root, "../secret.key"))

    def test_security_report_metrics(self):
        report = SecurityReport(target="/tmp/test")
        self.assertTrue(report.passed)
        self.assertEqual(report.security_score, 100)

        # Adiciona finding
        from kairos_security.models import Finding

        report.findings.append(
            Finding(
                id="TEST-001",
                title="Teste de Achado",
                description="Desc",
                severity=Severity.HIGH,
                category=Category.PERMISSIONS,
            )
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.security_score, 80)
        self.assertEqual(report.counts_by_severity["high"], 1)

    def test_formatters(self):
        report = run_full_audit(Path.cwd())
        cli_out = format_cli_summary(report)
        self.assertIn("KAIROS SECURITY AUDIT REPORT", cli_out)
        self.assertIn("Security Score:", cli_out)

        json_out = format_json_report(report)
        self.assertIn('"target":', json_out)
        self.assertIn('"findings":', json_out)


if __name__ == "__main__":
    unittest.main()
