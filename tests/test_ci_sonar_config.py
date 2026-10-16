"""Invariantes de configuração da análise Sonar advisory.

Lê `sonar-project.properties`, `pyproject.toml` e `.github/workflows/ci.yml`
como **dado de configuração** e afirma contratos entre eles — não lê código de
produção em runtime. Afirma propriedades (como duas fontes devem concordar),
nunca congela listas de regras nem contagens.
"""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
SONAR = REPO / "sonar-project.properties"
PYPROJECT = REPO / "pyproject.toml"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"


def parse_properties(text: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


def split_paths(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class SonarPropertiesTests(unittest.TestCase):
    def setUp(self):
        if not SONAR.is_file():
            self.fail(f"{SONAR} ausente")
        self.properties = parse_properties(SONAR.read_text(encoding="utf-8"))
        self.pyproject = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    def test_cobertura_aponta_para_o_relatorio_combinado(self):
        self.assertEqual(self.properties.get("sonar.python.coverage.reportPaths"), "coverage.xml")

    def test_gate_permanece_advisory(self):
        self.assertEqual(self.properties.get("sonar.qualitygate.wait"), "false")

    def test_fontes_do_projeto_declaradas(self):
        self.assertEqual(self.properties.get("sonar.sources"), ".")
        for chave in ("sonar.projectKey", "sonar.organization"):
            self.assertTrue(self.properties.get(chave), f"{chave} vazio")

    def test_exclusoes_cobrem_o_extend_exclude_do_ruff(self):
        ruff_excludes = self.pyproject["tool"]["ruff"]["extend-exclude"]
        exclusions = split_paths(self.properties.get("sonar.exclusions", ""))
        for exclusive in ruff_excludes:
            topo = exclusive.rstrip("/")
            coberto = any(path.rstrip("/").split("/")[0] == topo for path in exclusions)
            self.assertTrue(
                coberto,
                f"{exclusive!r} (extend-exclude do Ruff) fora de sonar.exclusions",
            )

    def test_omit_da_cobertura_espelhado_nas_exclusoes(self):
        omit = self.pyproject["tool"]["coverage"]["run"]["omit"]
        exclusions = split_paths(self.properties.get("sonar.exclusions", ""))
        for padrao in omit:
            topo = padrao.rstrip("/").split("/")[0]
            if topo in ("tests", "*"):
                # `tests/` não entra em sonar.exclusions: o scanner o classifica
                # como código de teste (heurística, ver WARN "sonar.tests"), não
                # o exclui da análise.
                continue
            coberto = any(path.rstrip("/").split("/")[0] == topo for path in exclusions)
            self.assertTrue(
                coberto,
                f"{padrao!r} (omit da cobertura) fora de sonar.exclusions",
            )

    def test_source_unica_na_raiz(self):
        source = self.pyproject["tool"]["coverage"]["run"]["source"]
        self.assertEqual(
            source,
            ["."],
            "o coverage xml relativiza filenames às raízes de `source`; N raízes "
            "geram basenames ambíguos que o Sonar ignora ('exists in several source')",
        )

    def test_duplicacao_exclui_testes(self):
        cpd = split_paths(self.properties.get("sonar.cpd.exclusions", ""))
        self.assertTrue(
            any(path.split("/")[0] == "tests" for path in cpd),
            "sonar.cpd.exclusions deveria excluir tests/**",
        )

    def test_token_nunca_vaza_para_o_arquivo(self):
        self.assertNotIn("SONAR_TOKEN", SONAR.read_text(encoding="utf-8"))

    def test_multicriteria_aponta_para_regras_python(self):
        regras = {
            chave: valor for chave, valor in self.properties.items() if chave.endswith(".ruleKey")
        }
        self.assertTrue(regras, "nenhum sonar.issue.ignore.multicriteria.*.ruleKey")
        for chave, valor in regras.items():
            self.assertRegex(valor, r"^python:S\d+$", chave)

    def test_multicriteria_restrito_a_python(self):
        recursos = {
            chave: valor
            for chave, valor in self.properties.items()
            if chave.endswith(".resourceKey")
        }
        self.assertTrue(recursos, "multicriteria sem resourceKey")
        for chave, valor in recursos.items():
            self.assertIn(".py", valor, chave)


class SonarWorkflowTests(unittest.TestCase):
    """Invariantes do job `sonar` no workflow, lidas como dado."""

    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.text)
        cls.jobs = cls.workflow["jobs"]

    def _steps(self, job: str) -> list[dict]:
        return self.jobs[job].get("steps", [])

    def test_sonar_depende_do_job_de_testes(self):
        self.assertIn("tests", self.jobs["sonar"].get("needs", []))

    def test_sonar_nao_reexecuta_a_suite(self):
        for step in self._steps("sonar"):
            comando = step.get("run", "")
            self.assertNotIn("pytest", comando, "o job sonar não deve reexecutar a suíte")

    def test_sonar_nao_bloqueia_o_deploy(self):
        self.assertNotIn("sonar", self.jobs["deploy"].get("needs", []))

    def test_artefato_de_cobertura_liga_os_dois_jobs(self):
        uploads = [
            step["with"].get("name")
            for step in self._steps("tests")
            if "upload-artifact" in step.get("uses", "")
        ]
        downloads = [
            step["with"].get("name")
            for step in self._steps("sonar")
            if "download-artifact" in step.get("uses", "")
        ]
        self.assertIn("coverage", uploads)
        self.assertIn("coverage", downloads)

    def test_actions_fixadas_por_sha(self):
        usos = re.findall(r"uses:\s*(\S+)", self.text)
        self.assertTrue(usos)
        for uso in usos:
            self.assertRegex(
                uso,
                r"^[^@]+@[0-9a-f]{40}$",
                f"action não fixada por SHA (40 hex): {uso}",
            )


class LocalParityTests(unittest.TestCase):
    """`scripts/ci.sh` espelha a cobertura e o scan condicional do CI.

    Lê o script como dado de configuração (é receita, não código de produção).
    """

    @classmethod
    def setUpClass(cls):
        cls.text = (REPO / "scripts" / "ci.sh").read_text(encoding="utf-8")

    def test_gera_cobertura(self):
        self.assertIn("coverage run", self.text)
        self.assertIn("coverage xml", self.text)

    def test_scan_do_sonar_e_condicional(self):
        self.assertIn("sonar-scanner", self.text)
        self.assertIn("SONAR_TOKEN", self.text)
        # Sem as ferramentas, o caminho é um `skip` — o mesmo padrão dos skips
        # de shellcheck/npm, nunca falha nem finge análise.
        self.assertRegex(self.text, r'skip\s+"Sonar[^\n]*')


if __name__ == "__main__":
    unittest.main()
