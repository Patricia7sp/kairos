"""Agente: laço de conversa, compactação e cliente auxiliar."""

from kairos_agent.auxiliary import (
    TEXT_CHAIN,
    VISION_CHAIN,
    AuxiliaryConfig,
    AuxiliaryTask,
    Backend,
    resolve_backend,
)
from kairos_agent.compaction import (
    OVERFLOW_REASONS,
    CompactionTrigger,
    FailoverReason,
    compaction_trigger,
    is_output_cap_error,
)
from kairos_agent.loop import (
    ExitReason,
    IterationBudget,
    LoopLimits,
    TurnOutcome,
    should_continue,
)
from kairos_agent.micro_compaction import MicroCompactionConfig, should_micro_compact

__all__ = [
    "OVERFLOW_REASONS",
    "TEXT_CHAIN",
    "VISION_CHAIN",
    "AuxiliaryConfig",
    "AuxiliaryTask",
    "Backend",
    "CompactionTrigger",
    "ExitReason",
    "FailoverReason",
    "IterationBudget",
    "LoopLimits",
    "MicroCompactionConfig",
    "TurnOutcome",
    "compaction_trigger",
    "is_output_cap_error",
    "resolve_backend",
    "should_continue",
    "should_micro_compact",
]
