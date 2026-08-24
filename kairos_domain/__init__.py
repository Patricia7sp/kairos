"""Modelo de domínio do Kairos (Tarefa 02)."""

from kairos_domain.identity import Platform, SessionSource
from kairos_domain.invariants import INVARIANTS, Enforcement, Invariant
from kairos_domain.machines import (
    MACHINES,
    STATEFUL_ENTITY_COUNT,
    SessionStatus,
    classify_session_status,
    machine,
    monitor_outcome,
)
from kairos_domain.message import (
    DomainRuleViolation,
    Message,
    Role,
    Visibility,
)
from kairos_domain.rules import RULES, Kind, Rule
from kairos_domain.session import ChildKind, EndReason, Lineage, Session
from kairos_domain.statemachine import IllegalTransition, StateMachine, Transition

__all__ = [
    "INVARIANTS",
    "MACHINES",
    "RULES",
    "STATEFUL_ENTITY_COUNT",
    "ChildKind",
    "DomainRuleViolation",
    "EndReason",
    "Enforcement",
    "IllegalTransition",
    "Invariant",
    "Kind",
    "Lineage",
    "Message",
    "Platform",
    "Role",
    "Rule",
    "Session",
    "SessionSource",
    "SessionStatus",
    "StateMachine",
    "Transition",
    "Visibility",
    "classify_session_status",
    "machine",
    "monitor_outcome",
]
