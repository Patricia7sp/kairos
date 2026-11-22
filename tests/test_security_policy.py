import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from kairos_cli.handlers import cmd_security
from kairos_security import run_full_audit
from kairos_security.formatter import format_cli_summary, format_json_report
from kairos_security.models import (
    Category,
    CheckError,
    CheckResult,
    CheckState,
    Finding,
    SecurityReport,
    Severity,
)
from kairos_security.policy import AuditProfile, assess_audit, evaluate_audit_policy


@pytest.mark.parametrize(
    ("collected", "severity", "execution", "policy"),
    [
        ((), None, "failed", "unknown"),
        (("snapshot_inventory",), None, "partial", "unknown"),
        (("snapshot_inventory",), Severity.HIGH, "partial", "fail"),
        (
            ("snapshot_inventory", "snapshot_secrets", "snapshot_sast"),
            Severity.CRITICAL,
            "completed",
            "fail",
        ),
        (("snapshot_inventory", "snapshot_secrets", "snapshot_sast"), None, "completed", "pass"),
    ],
)
def test_coleta_e_politica_sao_independentes(collected, severity, execution, policy):
    report = SecurityReport(
        "projeto",
        profile=AuditProfile.SNAPSHOT_STATIC,
        checks=tuple(CheckResult(c, CheckState.COMPLETED) for c in collected),
        findings=[Finding("ID", "Achado", "Descrição", severity, Category.SOURCE_CODE)]
        if severity
        else [],
    )
    result = assess_audit(report)
    assert result.execution_state == execution
    assert evaluate_audit_policy(report) == policy
    assert report.passed is (policy == "pass")
    assert result.coverage_complete is (execution == "completed")
    with pytest.raises(TypeError):
        evaluate_audit_policy(report, exceptions=())


def test_cli_separa_falha_da_politica_e_coleta_ausente(tmp_path, capsys):
    target = tmp_path / "runtime"
    args = argparse.Namespace(
        target=str(target), source=None, no_source=True, json=True, fail_on="high"
    )
    assert cmd_security(args) == 3
    missing = json.loads(capsys.readouterr().out)
    assert type(missing["schema_version"]) is int and missing["schema_version"] > 0
    assert missing["policy_outcome"] == "unknown" and missing["execution_state"] == "failed"
    assert missing["passed"] is False and not missing["coverage_complete"]
    target.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "example.py").write_text("try:\n    go()\nexcept Exception:\n    pass\n")
    args.source = str(source)
    args.no_source = False
    assert cmd_security(args) == 0
    default = json.loads(capsys.readouterr().out)
    assert default["passed"] and default["coverage_complete"]
    args.fail_on = "low"
    assert cmd_security(args) == 1
    strict = json.loads(capsys.readouterr().out)
    assert strict["policy_outcome"] == "fail" and strict["execution_state"] == "completed"
    assert strict["passed"] is False and strict["severity_threshold"] == "low"
    assert any(
        f["detector"] == "source_code" and f["fingerprint"] and f["line_number"] == 3
        for f in strict["findings"]
    )


def test_leitura_parcial_preserva_achados_e_oculta_erro(tmp_path, monkeypatch):
    (tmp_path / "match.py").write_text('KEY = "sk-ant-' + "Q7w_" * 12 + '"\n')
    (tmp_path / "blocked.py").write_text("pass\n")
    read = Path.read_text

    def selective(p, *args, **kwargs):
        if p == tmp_path / "blocked.py":
            raise PermissionError("detalhe privado")
        return read(p, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", selective)
    report = run_full_audit(tmp_path, scan_source=False)
    assert assess_audit(report).execution_state == "partial"
    assert evaluate_audit_policy(report, threshold=Severity.CRITICAL) == "fail"
    secret = next(c for c in report.checks if c.check_id == "secrets")
    assert (secret.state, secret.error) == (CheckState.PARTIAL, CheckError.READ_ERROR)
    assert "detalhe privado" not in repr(report)


def test_diretorio_sem_escopo_por_symlink_nao_vira_cobertura_completa(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "example.py").write_text("pass\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    linked = target / "application"
    linked.symlink_to(outside, target_is_directory=True)
    # Diretórios previstos na lista de exclusões não comprometem a cobertura.
    (target / "node_modules").symlink_to(outside, target_is_directory=True)
    original = Path.read_text

    def read(path, *args, **kwargs):
        assert path.name != "example.py", "o scanner seguiu um link de diretório"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    report = run_full_audit(target, source_root=target)
    assert evaluate_audit_policy(report) == "unknown"
    assert {(c.check_id, c.error) for c in report.checks if c.state == CheckState.PARTIAL} == {
        ("secrets", CheckError.READ_ERROR),
        ("source_code", CheckError.READ_ERROR),
    }
    linked.unlink()
    assert evaluate_audit_policy(run_full_audit(target, source_root=target)) == "pass"


def test_falha_na_importacao_nao_simula_api_configurada(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "kairos_web.server", None)
    report = run_full_audit(tmp_path, scan_source=False)
    result = assess_audit(report)
    api = next(c for c in result.checks if c.check_id == "api_surface")
    assert (api.state, api.error) == (CheckState.UNAVAILABLE, CheckError.DETECTOR_UNAVAILABLE)
    assert result.execution_state == "partial" and result.policy_outcome == "unknown"
    assert "INCONCLUSIVO" in format_cli_summary(report)


def test_status_cancelado_preserva_achados_e_cobertura():
    finding = Finding("ID", "Achado", "Descrição", Severity.HIGH, Category.SECRETS)
    report = SecurityReport("projeto", findings=[finding], cancelled=True)
    result = json.loads(format_json_report(report))
    assert result["execution_state"] == "cancelled"
    assert not result["coverage_complete"] and result["policy_outcome"] == "fail"
    assert (
        finding.fingerprint
        == replace(finding, evidence="omitida", severity=Severity.CRITICAL).fingerprint
    )
    assert finding.fingerprint != replace(finding, file_path="outro.py").fingerprint


def test_despacho_cli_nao_aceita_excecoes(tmp_path, capsys):
    from kairos_cli.main import main

    with pytest.raises(SystemExit) as error:
        main(["security", "--target", str(tmp_path), "--exceptions", "[]"])
    assert error.value.code == 2
    assert "--exceptions" in capsys.readouterr().err
