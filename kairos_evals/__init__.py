"""Evals: harness de recall de compactação e o gate de CI."""

from kairos_evals.recall import (
    DEFAULT_RECALL_THRESHOLD,
    Fact,
    FactKind,
    GateResult,
    RecallReport,
    RecallResult,
    check_gate,
    score_recall,
)

__all__ = [
    "DEFAULT_RECALL_THRESHOLD",
    "Fact",
    "FactKind",
    "GateResult",
    "RecallReport",
    "RecallResult",
    "check_gate",
    "score_recall",
]
