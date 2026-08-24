"""Host de compute da TUI: RPC e supervisão."""

from kairos_tui_host.rpc import (
    DuplicateMethod,
    HandlerRegistry,
    method,
    profile_scoped,
)
from kairos_tui_host.supervisor import (
    GIL_STARVATION_RATIONALE,
    HostState,
    HostSupervisor,
    RestartPolicy,
)

__all__ = [
    "GIL_STARVATION_RATIONALE",
    "DuplicateMethod",
    "HandlerRegistry",
    "HostState",
    "HostSupervisor",
    "RestartPolicy",
    "method",
    "profile_scoped",
]
