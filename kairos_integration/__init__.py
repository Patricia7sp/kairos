"""Integração das superfícies sobre o núcleo."""

from kairos_integration.composition import ComposedInteractionService, build_interaction_service
from kairos_integration.event_protocol import (
    INTERACTION_PROTOCOL_VERSION,
    interaction_event_to_json,
)
from kairos_integration.interaction_contract import (
    InteractionCost,
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
    "INTERACTION_PROTOCOL_VERSION",
    "PROCESS_TOPOLOGIES",
    "SURFACES",
    "SURFACE_PROFILES",
    "ComposedInteractionService",
    "CoreIsALibrary",
    "InteractionCost",
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
    "build_interaction_service",
    "interaction_event_to_json",
]
