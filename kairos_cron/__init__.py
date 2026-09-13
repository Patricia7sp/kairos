"""Cron: agendamento, claim, monitor."""

from kairos_cron.delivery import (
    DELIVERY_PAYLOAD_MAX_CHARS,
    TARGET_MAX_CHARS,
    DeliveryTargetError,
    cron_delivery_targets,
    record_cron_delivery,
    validate_delivery,
)
from kairos_cron.dispatch import (
    ClaimResult,
    DispatchClaimer,
    LifecycleGuardError,
    is_job_runnable,
    reject_gateway_restart_job,
)
from kairos_cron.lifecycle_guard import check_gateway_lifecycle
from kairos_cron.monitor import (
    MonitorDecision,
    MonitorOutcome,
    MonitorState,
    evaluate,
    output_hash,
    render_change_block,
)
from kairos_cron.schedule import (
    ScheduleKind,
    collapse_backlog,
    compute_next_run,
    croniter_available,
    normalize_repeat,
)

__all__ = [
    "DELIVERY_PAYLOAD_MAX_CHARS",
    "TARGET_MAX_CHARS",
    "ClaimResult",
    "DeliveryTargetError",
    "DispatchClaimer",
    "LifecycleGuardError",
    "MonitorDecision",
    "MonitorOutcome",
    "MonitorState",
    "ScheduleKind",
    "check_gateway_lifecycle",
    "collapse_backlog",
    "compute_next_run",
    "cron_delivery_targets",
    "croniter_available",
    "evaluate",
    "is_job_runnable",
    "normalize_repeat",
    "output_hash",
    "record_cron_delivery",
    "reject_gateway_restart_job",
    "render_change_block",
    "validate_delivery",
]
