"""Prova que a cobertura captura código executado em subprocessos.

O ``coverage`` (>=7.13) instala um ``.pth`` permanente que chama
``process_startup()`` em todo interpretador filho quando ``COVERAGE_PROCESS_START``
está definido. Sem isso o filho não é medido e o número da suíte sai
artificialmente baixo — este teste existe para impedir essa mentira.

Opt-in por ``KAIROS_COVERAGE_HARNESS=1``: spawna processos e escreve arquivos de
dados em tempdir. Prova o comportamento nos dois sentidos — com a variável o
filho é capturado, sem ela não é —, então um ``.pth`` ausente falha o caso
positivo (o RED).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CHILD = "import probe_pkg.worker as w; assert w.job() == 'executed-in-child'"


def _run(arguments, *, cwd, env=None):
    return subprocess.run(arguments, cwd=cwd, env=env, text=True, capture_output=True, check=False)


@unittest.skipUnless(
    os.environ.get("KAIROS_COVERAGE_HARNESS") == "1",
    "harness de cobertura é opt-in: defina KAIROS_COVERAGE_HARNESS=1",
)
class SubprocessCoverageTests(unittest.TestCase):
    def setUp(self):
        try:
            import coverage  # noqa: F401
        except ImportError:
            self.skipTest("coverage ausente; instale o extra dev")

    def _lay_out_probe(self, root: Path) -> Path:
        package = root / "probe_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "worker.py").write_text(
            "def job():\n    marker = 'executed-in-child'\n    return marker\n",
            encoding="utf-8",
        )
        (root / "driver.py").write_text(
            textwrap.dedent(
                f"""
                import subprocess
                import sys

                subprocess.run([sys.executable, "-c", {CHILD!r}], check=True)
                """
            ),
            encoding="utf-8",
        )
        return package

    def _write_covrc(self, root: Path, package: Path) -> Path:
        path = root / ".coveragerc"
        path.write_text(
            textwrap.dedent(
                f"""
                [run]
                source = {package}
                parallel = true
                branch = false
                data_file = {root / ".coverage"}
                """
            ),
            encoding="utf-8",
        )
        return path

    def _environment(self, root: Path, covrc: Path, *, process_start: bool) -> dict[str, str]:
        env = {
            **os.environ,
            "COVERAGE_FILE": str(root / ".coverage"),
            "PYTHONPATH": str(root),
        }
        if process_start:
            env["COVERAGE_PROCESS_START"] = str(covrc)
        else:
            env.pop("COVERAGE_PROCESS_START", None)
        return env

    def _measure(self, root: Path, covrc: Path, *, process_start: bool) -> dict:
        env = self._environment(root, covrc, process_start=process_start)
        run = _run(
            [sys.executable, "-m", "coverage", "run", "--parallel-mode", "driver.py"],
            cwd=root,
            env=env,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        combine = _run([sys.executable, "-m", "coverage", "combine"], cwd=root, env=env)
        self.assertEqual(combine.returncode, 0, combine.stderr)
        report = root / "report.json"
        produce = _run(
            [sys.executable, "-m", "coverage", "json", "-o", str(report)],
            cwd=root,
            env=env,
        )
        self.assertEqual(produce.returncode, 0, produce.stderr)
        return json.loads(report.read_text(encoding="utf-8"))

    @staticmethod
    def _worker_summary(data: dict) -> dict | None:
        for name, info in data["files"].items():
            if name.endswith("probe_pkg/worker.py"):
                return info["summary"]
        return None

    def _with_probe(self, *, process_start: bool) -> tuple[dict, dict | None]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            package = self._lay_out_probe(root)
            covrc = self._write_covrc(root, package)
            data = self._measure(root, covrc, process_start=process_start)
            return data, self._worker_summary(data)

    def test_child_process_lines_are_combined(self):
        data, summary = self._with_probe(process_start=True)
        self.assertIsNotNone(summary, f"worker.py ausente do relatório: {list(data['files'])}")
        assert summary is not None
        self.assertGreater(summary["covered_lines"], 0, "linhas do filho não capturadas")
        self.assertEqual(
            summary["covered_lines"],
            summary["num_statements"],
            "o corpo executado no filho não foi medido por inteiro",
        )

    def test_child_is_not_measured_without_process_start(self):
        _, summary = self._with_probe(process_start=False)
        covered = 0 if summary is None else summary["covered_lines"]
        self.assertEqual(covered, 0, "filho capturado mesmo sem COVERAGE_PROCESS_START")

    def test_project_config_measures_child_outside_project_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            project.mkdir()
            outside = root / "outside"
            outside.mkdir()
            self._lay_out_probe(project)
            dependency = project / ".venv"
            dependency.mkdir()
            (dependency / "dependency.py").write_text("value = 'dependency'\n", encoding="utf-8")
            child = CHILD + "; import dependency; assert dependency.value == 'dependency'"
            (project / "driver.py").write_text(
                "import probe_pkg.worker\n"
                "import subprocess, sys\n"
                f"subprocess.run([sys.executable, '-c', {child!r}], "
                f"cwd={str(outside)!r}, check=True)\n",
                encoding="utf-8",
            )
            config = REPO / "pyproject.toml"
            env = self._environment(project, config, process_start=True)
            env["KAIROS_COVERAGE_ROOT"] = str(project)
            env["PYTHONPATH"] = os.pathsep.join((str(project), str(dependency)))
            for arguments in (
                ["run", "driver.py"],
                ["combine"],
                ["json", "-o", "report.json"],
                ["xml", "-o", "coverage.xml"],
            ):
                result = _run(
                    [
                        sys.executable,
                        "-m",
                        "coverage",
                        arguments[0],
                        f"--rcfile={config}",
                        *arguments[1:],
                    ],
                    cwd=project,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads((project / "report.json").read_text(encoding="utf-8"))
            workers = [name for name in data["files"] if name.endswith("probe_pkg/worker.py")]
            self.assertEqual(workers, ["probe_pkg/worker.py"])
            summary = data["files"][workers[0]]["summary"]
            self.assertEqual(summary["covered_lines"], summary["num_statements"])
            self.assertFalse(any(".venv/" in name for name in data["files"]), data["files"])
            filenames = re.findall(
                r'<class\b[^>]*\bfilename="([^"]+)"',
                (project / "coverage.xml").read_text(encoding="utf-8"),
            )
            self.assertEqual(filenames.count("probe_pkg/worker.py"), 1)


if __name__ == "__main__":
    unittest.main()
