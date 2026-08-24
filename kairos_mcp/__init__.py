"""MCP: cliente e servidor."""

from kairos_mcp.client import (
    MCP_HARD_RESULT_CAP_CHARS,
    MCPServerConfig,
    SchemaCache,
    Transport,
    UnsafeServerConfig,
    mcp_available,
    namespaced_tool_name,
    truncate_mcp_text_result,
    validate_server_config,
)
from kairos_mcp.server import (
    AUTHENTICATION_RATIONALE,
    SERVER_TOOLS,
    UNPUBLISHED_TOOLS,
    EventBridge,
    server_tool_names,
)

__all__ = [
    "AUTHENTICATION_RATIONALE",
    "MCP_HARD_RESULT_CAP_CHARS",
    "SERVER_TOOLS",
    "UNPUBLISHED_TOOLS",
    "EventBridge",
    "MCPServerConfig",
    "SchemaCache",
    "Transport",
    "UnsafeServerConfig",
    "mcp_available",
    "namespaced_tool_name",
    "server_tool_names",
    "truncate_mcp_text_result",
    "validate_server_config",
]
