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
from kairos_security.auditor import find_source_root
from kairos_security.checks.path_traversal import check_path_traversal_guards
from kairos_security.checks.permissions import check_file_permissions
from kairos_security.checks.source_code import scan_source_tree


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


class SourceCodeScanTests(unittest.TestCase):
    """O SAST de código-fonte: o que ele acusa e, sobretudo, o que ele cala.

    Um scanner barulhento é pior que nenhum — o leitor aprende a ignorar a
    categoria inteira. Por isso metade destes testes é sobre falso-positivo.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _scan(self, filename: str, code: str):
        (self.root / filename).write_text(code, encoding="utf-8")
        return scan_source_tree(self.root)

    def _ids(self, findings):
        return {f.id for f in findings}

    # --- deve acusar ---

    def test_flags_bind_to_all_interfaces(self):
        f = self._scan("srv.py", 'host = "0.0.0.0"\n')
        self.assertIn("SEC-SRC-001", self._ids(f))
        self.assertEqual(f[0].severity, Severity.HIGH)
        self.assertEqual(f[0].line_number, 1)

    def test_flags_hardcoded_token_including_dict_key_form(self):
        f = self._scan("srv.py", 'x = {"token": "kairos-session-token"}\n')
        self.assertIn("SEC-SRC-002", self._ids(f))

    def test_flags_shell_true_and_eval(self):
        f = self._scan("r.py", "subprocess.run(cmd, shell=True)\neval(user_input)\n")
        self.assertEqual({"SEC-SRC-004", "SEC-SRC-005"}, self._ids(f))

    def test_flags_timing_unsafe_secret_comparison(self):
        f = self._scan("a.py", "if token == expected:\n    pass\n")
        self.assertIn("SEC-SRC-008", self._ids(f))

    def test_flags_broad_except_even_when_linter_is_silenced(self):
        """`# noqa` cala o ruff, não a auditoria de segurança."""
        f = self._scan("a.py", "try:\n    go()\nexcept Exception:  # noqa: BLE001\n    pass\n")
        self.assertIn("SEC-SRC-011", self._ids(f))

    # --- NÃO deve acusar ---

    def test_ignores_narrow_typed_except_as_control_flow(self):
        code = "for enc in encs:\n    try:\n        return d.decode(enc)\n    except UnicodeDecodeError:\n        continue\n"
        self.assertNotIn("SEC-SRC-011", self._ids(self._scan("a.py", code)))

    def test_ignores_constant_whose_value_echoes_its_own_name(self):
        f = self._scan("e.py", 'DIRECT_API_KEY = "direct_api_key"\n')
        self.assertNotIn("SEC-SRC-002", self._ids(f))

    def test_ignores_header_name_constant(self):
        f = self._scan("h.py", 'TOKEN_HEADER = "X-Hermes-Session-Token"\n')
        self.assertNotIn("SEC-SRC-002", self._ids(f))

    def test_ignores_tokenize_which_merely_starts_with_token(self):
        f = self._scan("s.py", "sql = \"tokenize='unicode61'\"\n")
        self.assertNotIn("SEC-SRC-002", self._ids(f))

    def test_ignores_md5_marked_as_not_for_security(self):
        f = self._scan("k.py", "h = hashlib.md5(usedforsecurity=False)\n")
        self.assertNotIn("SEC-SRC-009", self._ids(f))

    def test_ignores_test_fixtures_with_obvious_fake_keys(self):
        f = self._scan("test_x.py", 'adapter = Adapter(api_key="sk-ant-test")\n')
        self.assertNotIn("SEC-SRC-002", self._ids(f))

    def test_ignores_literal_inside_a_comment(self):
        f = self._scan("c.py", '# exemplo: host = "0.0.0.0"\n')
        self.assertEqual(set(), self._ids(f))

    def test_does_not_audit_its_own_rule_definitions(self):
        """source_code.py contém todos os padrões como literais."""
        findings = scan_source_tree(Path(__file__).resolve().parent.parent)
        self.assertEqual([], [f for f in findings if "source_code.py" in (f.file_path or "")])

    # --- o repositório real ---

    def test_this_repository_has_no_high_severity_source_findings(self):
        root = find_source_root()
        self.assertIsNotNone(root, "raiz do repositório deveria ser encontrada")
        severe = [
            f
            for f in scan_source_tree(root)
            if f.severity in (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM)
        ]
        self.assertEqual([], severe, f"achados severos no código-fonte: {severe}")


class SourceRootDiscoveryTests(unittest.TestCase):
    def test_finds_repo_root_by_pyproject(self):
        root = find_source_root()
        self.assertIsNotNone(root)
        self.assertTrue((root / "pyproject.toml").is_file())

    def test_audit_reports_the_source_root_it_used(self):
        report = run_full_audit(Path.cwd())
        self.assertIsNotNone(report.source_root)
        self.assertIn("Código-fonte:", format_cli_summary(report))
        self.assertIn('"source_root":', format_json_report(report))

    def test_no_source_flag_skips_the_code_scan(self):
        report = run_full_audit(Path.cwd(), scan_source=False)
        self.assertIsNone(report.source_root)
        self.assertEqual([], [f for f in report.findings if f.category == Category.SOURCE_CODE])


if __name__ == "__main__":
    unittest.main()
