from kairos_security.models import CheckResult, CheckState, SecurityReport
from kairos_security.policy import AuditProfile, assess_audit, evaluate_audit_policy


def test_detector_indisponivel_nao_vira_inaplicavel():
    for profile, observed in (
        (
            AuditProfile.DEPENDENCY_ADVISORIES,
            CheckResult("dependency_inventory", CheckState.COMPLETED, applicable_manifests=1),
        ),
        (AuditProfile.RUNTIME_CONFIG, CheckResult("runtime_configuration", CheckState.COMPLETED)),
    ):
        report = SecurityReport("projeto", profile=profile, checks=(observed,))
        result = assess_audit(report)
        assert not result.coverage_complete
        assert evaluate_audit_policy(report) == "unknown"


def test_apenas_inventario_concluido_sem_manifestos_justifica_dispensa():
    advisory = CheckResult("dependency_advisories", CheckState.NOT_APPLICABLE)
    for state, count, expected in (
        (CheckState.COMPLETED, 0, "pass"),
        (CheckState.COMPLETED, 1, "unknown"),
        (CheckState.PARTIAL, 0, "unknown"),
        (CheckState.COMPLETED, None, "unknown"),
    ):
        inventory = CheckResult("dependency_inventory", state, applicable_manifests=count)
        report = SecurityReport(
            "projeto", profile=AuditProfile.DEPENDENCY_ADVISORIES, checks=(inventory, advisory)
        )
        assert evaluate_audit_policy(report) == expected


def test_check_duplicado_ou_n_a_sem_criterio_nao_aprova():
    runtime = CheckResult("runtime_configuration", CheckState.COMPLETED)
    for isolation in (
        (CheckResult("runtime_isolation", CheckState.NOT_APPLICABLE),),
        (runtime, CheckResult("runtime_isolation", CheckState.COMPLETED)),
    ):
        report = SecurityReport(
            "projeto", profile=AuditProfile.RUNTIME_CONFIG, checks=(runtime, *isolation)
        )
        assert evaluate_audit_policy(report) == "unknown"
