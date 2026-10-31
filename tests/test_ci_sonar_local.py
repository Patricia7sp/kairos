"""O CI local só exporta ao Sonar após selecionar e atestar o destino."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


class LocalSonarScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.checkout = self.root / "checkout"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.checkout.mkdir()
        self.bash = shutil.which("bash")
        self.git_binary = shutil.which("git")
        if self.bash is None or self.git_binary is None:
            self.fail("Os testes exigem Bash e Git reais.")
        for name in ("bash", "git", "dirname", "env"):
            executable = shutil.which(name)
            self.assertIsNotNone(executable)
            (self.bin / name).symlink_to(executable)
        self.environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("GIT_", "SONAR_")) and key not in {"BASH_ENV", "ENV"}
        }
        self.log = self.root / "scanner-arguments"
        self.environment.update(
            PATH=str(self.bin),
            UV=str(self.bin / "uv"),
            SONAR_TOKEN=str(self.root / "fixture-token"),
            SCANNER_LOG=str(self.log),
            SCANNER_EXIT_CODE="0",
            GIT_CONFIG_GLOBAL=os.devnull,
            GIT_CONFIG_NOSYSTEM="1",
        )
        self._command("uv", "exit 0\n")
        self._command(
            "sonar-scanner",
            'printf "%s\\0" "$@" > "$SCANNER_LOG"\nexit "$SCANNER_EXIT_CODE"\n',
        )
        scripts = self.checkout / "scripts"
        scripts.mkdir()
        shutil.copyfile(REPO / "scripts/ci.sh", scripts / "ci.sh")
        for version in (8, 10):
            (scripts / f"prepare-skill-v{version}-client.sh").write_text(
                f"#!{self.bash}\nexit 0\n", encoding="utf-8"
            )
        (self.checkout / "README.md").write_text("fixture\n", encoding="utf-8")
        self.git("init", "--quiet", "-b", "feature/sonar-scope")
        self.git("add", ".")
        self.git(
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "-qm",
            "fixture",
        )
        self.revision = self.git("rev-parse", "HEAD").strip()

    def _command(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{self.bash}\n{body}", encoding="utf-8")
        path.chmod(0o700)

    def git(self, *arguments):
        return subprocess.check_output(
            [self.git_binary, *arguments],
            cwd=self.checkout,
            env=self.environment,
            text=True,
            stderr=subprocess.PIPE,
        )

    def run_ci(self, *arguments):
        return subprocess.run(
            [self.bash, "scripts/ci.sh", "--fast", *arguments],
            cwd=self.checkout,
            env=self.environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )

    def arguments(self):
        self.assertTrue(self.log.is_file(), "O scanner não recebeu a análise solicitada.")
        arguments = self.log.read_bytes().split(b"\0")[:-1]
        return dict(argument.decode().removeprefix("-D").split("=", 1) for argument in arguments)

    def test_token_herdado_nao_inicia_scan_sem_destino(self):
        result = self.run_ci()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists(), "O CI iniciou um scan sem selecionar destino.")
        self.assertIn("pulado", result.stdout)

    def test_pr_recebe_branch_base_e_revisao_do_checkout(self):
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.arguments()
        self.assertEqual(arguments["sonar.pullrequest.key"], "124")
        self.assertEqual(arguments["sonar.pullrequest.branch"], "feature/sonar-scope")
        self.assertEqual(arguments["sonar.pullrequest.base"], "main")
        self.assertEqual(arguments["sonar.scm.revision"], self.revision)
        self.assertNotIn("sonar.branch.name", arguments)
        self.assertNotIn(self.environment["SONAR_TOKEN"], self.log.read_text())

    def test_main_explicita_recebe_branch_e_revisao(self):
        self.git("branch", "-m", "main")
        result = self.run_ci("--sonar-main")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.arguments()
        self.assertEqual(arguments["sonar.branch.name"], "main")
        self.assertEqual(arguments["sonar.scm.revision"], self.revision)
        self.assertFalse(any(key.startswith("sonar.pullrequest.") for key in arguments))

    def test_main_pedida_em_feature_recusa_sem_scanner(self):
        result = self.run_ci("--sonar-main")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_pr_pedido_na_main_recusa_sem_scanner(self):
        self.git("branch", "-m", "main")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_head_destacado_recusa_sem_scanner(self):
        self.git("checkout", "--quiet", "--detach")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_arquivo_modificado_recusa_sem_scanner(self):
        (self.checkout / "README.md").write_text("modificado\n")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_arquivo_staged_recusa_sem_scanner(self):
        (self.checkout / "README.md").write_text("modificado\n")
        self.git("add", "README.md")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_arquivo_nao_rastreado_recusa_sem_scanner(self):
        (self.checkout / "novo.txt").write_text("novo\n")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_configuracao_git_nao_oculta_arquivo_nao_rastreado(self):
        self.git("config", "status.showUntrackedFiles", "no")
        (self.checkout / "novo.txt").write_text("novo\n")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_git_de_outro_checkout_recusa_sem_scanner(self):
        other = self.root / "other"
        self.git("clone", "--quiet", "--local", str(self.checkout), str(other))
        self.environment.update(GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(other))
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_git_externo_com_raiz_local_recusa_sem_scanner(self):
        other = self.root / "other"
        self.git("clone", "--quiet", "--local", str(self.checkout), str(other))
        self.git(
            "-C",
            str(other),
            "-c",
            "user.name=fixture",
            "-c",
            "user.email=fixture@invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "outro commit",
        )
        self.environment.update(GIT_DIR=str(other / ".git"), GIT_WORK_TREE=str(self.checkout))
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_worktree_vinculada_preserva_branch_e_revisao(self):
        linked = self.root / "linked"
        self.git("worktree", "add", "--quiet", "-b", "feature/linked", str(linked))
        self.checkout = linked
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.arguments()
        self.assertEqual(arguments["sonar.pullrequest.branch"], "feature/linked")
        self.assertEqual(arguments["sonar.scm.revision"], self.revision)

    def test_overrides_git_recusam_sem_scanner(self):
        for key, value in (
            ("GIT_DIR", str(self.checkout / ".git")),
            ("GIT_WORK_TREE", str(self.checkout)),
            ("GIT_COMMON_DIR", str(self.checkout / ".git")),
            ("GIT_INDEX_FILE", str(self.checkout / ".git/index")),
        ):
            with self.subTest(variable=key):
                self.environment[key] = value
                try:
                    result = self.run_ci("--sonar-pr", "124")
                    self.assertEqual(result.returncode, 1)
                    self.assertFalse(self.log.exists())
                finally:
                    self.environment.pop(key)
                    self.log.unlink(missing_ok=True)

    def test_status_git_ilegivel_recusa_sem_scanner(self):
        (self.checkout / ".git/index").write_bytes(b"invalid")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.log.exists())

    def test_token_ausente_produz_skip_sem_scanner(self):
        self.environment.pop("SONAR_TOKEN")
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertIn("pulado", result.stdout)

    def test_scanner_ausente_produz_skip(self):
        (self.bin / "sonar-scanner").unlink()
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.log.exists())
        self.assertIn("pulado", result.stdout)

    def test_falha_do_scanner_faz_o_ci_falhar(self):
        self.environment["SCANNER_EXIT_CODE"] = "23"
        result = self.run_ci("--sonar-pr", "124")
        self.assertEqual(result.returncode, 1)
        self.assertTrue(self.log.is_file())
        self.assertIn("FALHOU", result.stdout)

    def test_seletores_invalidos_recusam_sem_scanner(self):
        for arguments in (
            ("--sonar-pr",),
            ("--sonar-pr", "0"),
            ("--sonar-pr", "abc"),
            ("--sonar-pr", "-Dsonar.branch.name=main"),
            ("--sonar-main", "--sonar-pr", "124"),
            ("--sonar-pr", "124", "--sonar-main"),
            ("--desconhecido",),
        ):
            with self.subTest(arguments=arguments):
                result = self.run_ci(*arguments)
                self.assertEqual(result.returncode, 2)
                self.assertFalse(self.log.exists())
