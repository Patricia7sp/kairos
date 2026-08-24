"""Auditoria de segurança de superfície de API (CORS, CSP e Headers)."""

from __future__ import annotations

from kairos_security.models import Category, Finding, Severity


def check_api_security_configuration(cors_origins: list[str]) -> list[Finding]:
    findings: list[Finding] = []

    if "*" in cors_origins:
        findings.append(
            Finding(
                id="SEC-API-001",
                title="CORS irrestrito (Wildcard *) detectado na API",
                description="A API permite qualquer origem (*), o que pode facilitar CSRF em navegadores.",
                severity=Severity.MEDIUM,
                category=Category.API_SURFACE,
                remediation="Restrinja allow_origins apenas para os domínios locais autorizados.",
                evidence="allow_origins: ['*']",
            )
        )

    return findings
