"""Integração das superfícies sobre o núcleo."""

from kairos_integration.interaction_contract import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionEventKind,
    InteractionResult,
    InteractionSelectionSnapshot,
    InteractionToolResult,
)
from kairos_integration.surfaces import (
    PROCESS_TOPOLOGIES,
    SURFACE_PROFILES,
    SURFACES,
    CoreIsALibrary,
    LawViolation,
    ProcessMap,
    ProcessTopology,
    Surface,
    SurfaceProfile,
    assert_narrow_waist,
    assert_prompt_cache_intact,
)

__all__ = [
    "PROCESS_TOPOLOGIES",
    "SURFACES",
    "SURFACE_PROFILES",
    "CoreIsALibrary",
    "InteractionEnvelope",
    "InteractionEvent",
    "InteractionEventKind",
    "InteractionResult",
    "InteractionSelectionSnapshot",
    "InteractionToolResult",
    "LawViolation",
    "ProcessMap",
    "ProcessTopology",
    "Surface",
    "SurfaceProfile",
    "assert_narrow_waist",
    "assert_prompt_cache_intact",
]
