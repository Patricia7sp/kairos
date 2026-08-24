"""Gateway: contrato de plataforma, streaming e entrega."""

from kairos_gateway.capability import (
    CapabilityDescriptor,
    MarkdownDialect,
    Operation,
    WakeupMode,
)
from kairos_gateway.delivery import (
    COOLDOWN_LADDER,
    DeadTargets,
    DeliveryLedger,
    DeliveryState,
    FailureKind,
    Obligation,
)
from kairos_gateway.service import (
    DRAIN_MARKER,
    GatewayService,
    PlatformAdapter,
    SendResult,
    write_drain_request,
)
from kairos_gateway.stream_events import (
    STREAM_EVENTS,
    Commentary,
    GatewayNotice,
    LongToolHint,
    MessageChunk,
    MessageStop,
    StreamEvent,
    ToolCallChunk,
    ToolCallFinished,
)

__all__ = [
    "COOLDOWN_LADDER",
    "DRAIN_MARKER",
    "STREAM_EVENTS",
    "CapabilityDescriptor",
    "Commentary",
    "DeadTargets",
    "DeliveryLedger",
    "DeliveryState",
    "FailureKind",
    "GatewayNotice",
    "GatewayService",
    "LongToolHint",
    "MarkdownDialect",
    "MessageChunk",
    "MessageStop",
    "Obligation",
    "Operation",
    "PlatformAdapter",
    "SendResult",
    "StreamEvent",
    "ToolCallChunk",
    "ToolCallFinished",
    "WakeupMode",
    "write_drain_request",
]
