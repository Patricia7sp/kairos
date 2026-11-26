"""Perfis, cobertura e avaliação determinística da auditoria."""

from dataclasses import dataclass
from enum import StrEnum, auto

from kairos_security.models import (
    AuditProfile,
    CheckError,
    CheckResult,
    CheckState,
    SecurityReport,
    Severity,
)

_LOCAL = (
    "permissions",
    "secrets",
    "path_traversal",
    "agent_guardrails",
    "api_surface",
    "prompt_injection",
)
_REQUIRED = {
    AuditProfile.LOCAL_FULL: (*_LOCAL, "source_code"),
    AuditProfile.LOCAL_RUNTIME: _LOCAL,
    AuditProfile.SNAPSHOT_STATIC: ("snapshot_inventory", "snapshot_secrets", "snapshot_sast"),
    AuditProfile.DEPENDENCY_ADVISORIES: ("dependency_inventory", "dependency_advisories"),
    AuditProfile.RUNTIME_CONFIG: ("runtime_configuration", "runtime_isolation"),
}
PROFILE_VERSION = 1
AUDIT_POLICY_VERSION = 1
_SEVERITY_ORDER = (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO)


class ExecutionState(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PolicyOutcome(StrEnum):
    PASS = auto()
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class AuditAssessment:
    execution_state: ExecutionState
    policy_outcome: PolicyOutcome
    coverage_complete: bool
    severity_threshold: Severity
    checks: tuple[CheckResult, ...]


def _coverage(report: SecurityReport) -> tuple[CheckResult, ...]:
    grouped: dict[str, list[CheckResult]] = {}
    for check in report.checks:
        grouped.setdefault(check.check_id, []).append(check)
    inventory = grouped.get("dependency_inventory", [])
    no_manifests = (
        len(inventory) == 1
        and inventory[0].state is CheckState.COMPLETED
        and inventory[0].error is None
        and type(inventory[0].applicable_manifests) is int
        and inventory[0].applicable_manifests == 0
    )
    coverage = []
    for check_id in _REQUIRED[report.profile]:
        results = grouped.get(check_id, [])
        if not results:
            coverage.append(
                CheckResult(check_id, CheckState.UNAVAILABLE, CheckError.MISSING_RESULT)
            )
            continue
        check = results[0]
        count = check.applicable_manifests
        valid = (
            len(results) == 1
            and isinstance(check.state, CheckState)
            and (check.error is None or isinstance(check.error, CheckError))
            and (
                count is None
                or (check_id == "dependency_inventory" and type(count) is int and count >= 0)
            )
        )
        if check.state is CheckState.NOT_APPLICABLE:
            valid &= check_id == "dependency_advisories" and no_manifests and check.error is None
        elif check.state is CheckState.COMPLETED:
            valid &= check.error is None
            if check_id == "dependency_inventory":
                valid &= count is not None
        coverage.append(
            check if valid else CheckResult(check_id, CheckState.FAILED, CheckError.INVALID_RESULT)
        )
    return tuple(coverage)


def assess_audit(report: SecurityReport, *, threshold: Severity = Severity.HIGH) -> AuditAssessment:
    return _assess_audit(report, threshold=threshold)


def _assess_audit(
    report: SecurityReport,
    *,
    threshold: Severity = Severity.HIGH,
    validated_indices: frozenset[int] = frozenset(),
) -> AuditAssessment:
    checks = _coverage(report)
    complete = all(c.state in (CheckState.COMPLETED, CheckState.NOT_APPLICABLE) for c in checks)
    if report.cancelled:
        execution = ExecutionState.CANCELLED
    elif complete:
        execution = ExecutionState.COMPLETED
    elif any(c.state in (CheckState.COMPLETED, CheckState.PARTIAL) for c in checks):
        execution = ExecutionState.PARTIAL
    else:
        execution = ExecutionState.FAILED

    cutoff = max(_SEVERITY_ORDER.index(Severity.HIGH), _SEVERITY_ORDER.index(threshold))
    if any(
        f.severity in _SEVERITY_ORDER[: cutoff + 1] and i not in validated_indices
        for i, f in enumerate(report.findings)
    ):
        outcome = PolicyOutcome.FAIL
    elif execution is ExecutionState.COMPLETED:
        outcome = PolicyOutcome.PASS
    else:
        outcome = PolicyOutcome.UNKNOWN
    return AuditAssessment(execution, outcome, complete, _SEVERITY_ORDER[cutoff], checks)


def evaluate_audit_policy(
    report: SecurityReport, *, threshold: Severity = Severity.HIGH
) -> PolicyOutcome:
    return assess_audit(report, threshold=threshold).policy_outcome
