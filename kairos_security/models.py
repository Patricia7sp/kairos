"""Modelos de dados para o subsistema de auditoria de segurança do Kairos."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Category(StrEnum):
    PERMISSIONS = "permissions"
    SECRETS = "secrets"
    PATH_TRAVERSAL = "path_traversal"
    AGENT_GUARDRAILS = "agent_guardrails"
    API_SURFACE = "api_surface"
    PROMPT_INJECTION = "prompt_injection"
    SOURCE_CODE = "source_code"


@dataclass(frozen=True)
class Finding:
    id: str
    title: str
    description: str
    severity: Severity
    category: Category
    file_path: str | None = None
    line_number: int | None = None
    remediation: str | None = None
    evidence: str | None = None


@dataclass
class SecurityReport:
    target: str
    source_root: str | None = None
    timestamp: float = field(default_factory=time.time)
    findings: list[Finding] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def passed(self) -> bool:
        return not any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in self.findings)

    @property
    def counts_by_severity(self) -> dict[str, int]:
        counts = {s.value: 0 for s in Severity}
        for f in self.findings:
            counts[f.severity.value] += 1
        return counts

    @property
    def security_score(self) -> int:
        """Calcula uma pontuação de segurança de 0 a 100 baseada nos achados."""
        deductions = {
            Severity.CRITICAL: 40,
            Severity.HIGH: 20,
            Severity.MEDIUM: 8,
            Severity.LOW: 2,
            Severity.INFO: 0,
        }
        score = 100
        for f in self.findings:
            score -= deductions.get(f.severity, 0)
        return max(0, score)
