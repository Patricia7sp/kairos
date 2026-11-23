"""Formatadores do relatório de segurança: resumo de terminal e JSON versionado."""

from __future__ import annotations

import json

from kairos_security.models import CheckState, SecurityReport, Severity
from kairos_security.policy import PROFILE_VERSION, PolicyOutcome, assess_audit

SEVERITY_ICONS = {
    Severity.CRITICAL: "🔴 CRÍTICO",
    Severity.HIGH: "🟠 ALTO",
    Severity.MEDIUM: "🟡 MÉDIO",
    Severity.LOW: "🔵 BAIXO",
    Severity.INFO: "ℹ️ INFO",
}


def format_cli_summary(report: SecurityReport, *, threshold: Severity = Severity.HIGH) -> str:
    result = assess_audit(report, threshold=threshold)
    status = {
        PolicyOutcome.PASS: "APROVADO",
        PolicyOutcome.FAIL: "REPROVADO",
        PolicyOutcome.UNKNOWN: "INCONCLUSIVO",
    }
    lines = [
        "============================================================",
        "          🛡️  KAIROS SECURITY AUDIT REPORT                  ",
        "============================================================",
        f"Alvo auditado:  {report.target}",
        f"Código-fonte:   {report.source_root or '(não auditado)'}",
        f"Duração:        {report.duration_ms} ms",
        f"Perfil:         {report.profile} (v{PROFILE_VERSION})",
        f"Coleta:         {result.execution_state}",
        f"Cobertura:      {'completa' if result.coverage_complete else 'incompleta'}",
        f"Security Score: {report.security_score}/100 (somente achados observados)",
        f"Status Geral:   {status[result.policy_outcome]} (limiar: {result.severity_threshold})",
        "------------------------------------------------------------",
        "Resumo de Vulnerabilidades:",
    ]
    for sev, count in report.counts_by_severity.items():
        lines.append(f"  • {sev.upper():<10}: {count}")
    lines.append("------------------------------------------------------------")
    for check in result.checks:
        if check.state is not CheckState.COMPLETED:
            reason = check.error or (
                "inventário sem manifestos aplicáveis"
                if check.state is CheckState.NOT_APPLICABLE
                else None
            )
            lines.append(f"  {check.check_id}: {check.state}" + (f" ({reason})" if reason else ""))

    if not report.findings:
        lines.append("Nenhum achado nos checks executados.")
    else:
        lines.append("Achados Detalhados:")
        for idx, f in enumerate(report.findings, start=1):
            icon = SEVERITY_ICONS.get(f.severity, f.severity.value)
            lines.append(f"\n[{idx}] {icon} - {f.title} ({f.id})")
            lines.append(f"    Categoria:   {f.category.value}")
            if f.file_path:
                lines.append(
                    f"    Arquivo:     {f.file_path}"
                    + (f":{f.line_number}" if f.line_number else "")
                )
            lines.append(f"    Descrição:   {f.description}")
            if f.remediation:
                lines.append(f"    Remediação:  {f.remediation}")

    lines.append("============================================================")
    return "\n".join(lines)


def format_json_report(report: SecurityReport, *, threshold: Severity = Severity.HIGH) -> str:
    result = assess_audit(report, threshold=threshold)
    data = {
        "schema_version": 2,
        "profile": report.profile,
        "profile_version": PROFILE_VERSION,
        "execution_state": result.execution_state,
        "policy_outcome": result.policy_outcome,
        "severity_threshold": result.severity_threshold,
        "coverage_complete": result.coverage_complete,
        "checks": [
            {
                "check_id": c.check_id,
                "detector": c.check_id,
                "detector_version": 1,
                "required": True,
                "state": c.state,
                "error": c.error,
                "not_applicable_reason": "no_applicable_manifests"
                if c.state is CheckState.NOT_APPLICABLE
                else None,
                "applicable_manifests": c.applicable_manifests,
            }
            for c in result.checks
        ],
        "target": report.target,
        "source_root": report.source_root,
        "timestamp": report.timestamp,
        "duration_ms": report.duration_ms,
        "score": report.security_score,
        "passed": result.policy_outcome is PolicyOutcome.PASS,
        "summary": report.counts_by_severity,
        "findings": [
            {
                "id": f.id,
                "title": f.title,
                "description": f.description,
                "severity": f.severity.value,
                "category": f.category.value,
                "file_path": f.file_path,
                "line_number": f.line_number,
                "remediation": f.remediation,
                "evidence": f.evidence,
                "fingerprint": f.fingerprint,
                "detector": f.detector,
                "detector_version": f.detector_version,
                "confidence": f.confidence,
            }
            for f in report.findings
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
