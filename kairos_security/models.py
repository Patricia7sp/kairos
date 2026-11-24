"""Modelos de dados para o subsistema de auditoria de segurança do Kairos."""

from __future__ import annotations

import hashlib
import json
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


class AuditProfile(StrEnum):
    LOCAL_FULL = "local_runtime_and_source"
    LOCAL_RUNTIME = "local_runtime"
    SNAPSHOT_STATIC = "snapshot_static"
    DEPENDENCY_ADVISORIES = "dependency_advisories"
    RUNTIME_CONFIG = "runtime_config"


class CheckState(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"


class CheckError(StrEnum):
    TARGET_UNAVAILABLE = "target_unavailable"
    DETECTOR_UNAVAILABLE = "detector_unavailable"
    READ_ERROR = "read_error"
    COLLECTION_ERROR = "collection_error"
    MISSING_RESULT = "missing_result"
    INVALID_RESULT = "invalid_result"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Produtores de `PARTIAL` devem informar a causa em `error`."""

    check_id: str
    state: CheckState
    error: CheckError | None = None
    applicable_manifests: int | None = None


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
    detector: str | None = None
    detector_version: int | None = None
    confidence: str = "unknown"

    @property
    def fingerprint(self) -> str:
        # Não incorporar texto de evidência, descrição, timestamp ou revisão.
        identity = (
            self.detector,
            self.id,
            self.title,
            self.category,
            self.file_path,
            self.line_number,
        )
        return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


@dataclass
class SecurityReport:
    target: str
    source_root: str | None = None
    timestamp: float = field(default_factory=time.time)
    findings: list[Finding] = field(default_factory=list)
    duration_ms: float = 0.0
    profile: AuditProfile = AuditProfile.LOCAL_FULL
    checks: tuple[CheckResult, ...] = ()
    # Reservado ao futuro produtor de cancelamento; o coletor local não o sinaliza.
    cancelled: bool = False
    target_revision: str | None = None
    scope_notes: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        from kairos_security.policy import PolicyOutcome, evaluate_audit_policy

        return evaluate_audit_policy(self) is PolicyOutcome.PASS

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
