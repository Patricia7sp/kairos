"""Invariantes de configuração da análise Sonar advisory.

Lê `sonar-project.properties`, `pyproject.toml` e `.github/workflows/ci.yml`
como **dado de configuração** e afirma contratos entre eles — não lê código de
produção em runtime. Afirma propriedades (como duas fontes devem concordar),
nunca congela listas de regras nem contagens.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
