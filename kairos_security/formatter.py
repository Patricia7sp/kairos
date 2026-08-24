"""Formatadores de relatório de segurança (CLI Markdown / JSON / SARIF)."""

from __future__ import annotations

import json

from kairos_security.models import SecurityReport, Severity

SEVERITY_ICONS = {
    Severity.CRITICAL: "🔴 CRÍTICO",
    Severity.HIGH: "🟠 ALTO",
    Severity.MEDIUM: "🟡 MÉDIO",
    Severity.LOW: "🔵 BAIXO",
    Severity.INFO: "ℹ️ INFO",
}


def format_cli_summary(report: SecurityReport) -> str:
    lines = [
        "============================================================",
        "          🛡️  KAIROS SECURITY AUDIT REPORT                  ",
        "============================================================",
        f"Alvo auditado:  {report.target}",
        f"Código-fonte:   {report.source_root or '(não auditado)'}",
        f"Duração:        {report.duration_ms} ms",
        f"Security Score: {report.security_score}/100",
        f"Status Geral:   {'✅ APROVADO' if report.passed else '❌ REQUER ATENÇÃO'}",
        "------------------------------------------------------------",
        "Resumo de Vulnerabilidades:",
    ]
    for sev, count in report.counts_by_severity.items():
        lines.append(f"  • {sev.upper():<10}: {count}")
    lines.append("------------------------------------------------------------")

    if not report.findings:
        lines.append("Nenhuma vulnerabilidade ou problema de configuração encontrado! 🎉")
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


def format_json_report(report: SecurityReport) -> str:
    data = {
        "target": report.target,
        "source_root": report.source_root,
        "timestamp": report.timestamp,
        "duration_ms": report.duration_ms,
        "score": report.security_score,
        "passed": report.passed,
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
            }
            for f in report.findings
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
