"""Allowlisted runtime payloads and fixed public error messages."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import RUNTIME_ERROR_CODES

__all__ = ["public_error", "sanitize_payload"]


_PUBLIC_ERRORS: dict[str, tuple[str, bool]] = {
    "unavailable": ("runtime indisponível", True),
    "incompatible": ("runtime incompatível", False),
    "thread_missing": ("thread de runtime ausente", False),
    "invalid_directory": ("diretório de runtime inválido", False),
    "invalid_policy": ("política de runtime inválida", False),
    "session_busy": ("runtime possui trabalho pendente", True),
    "idempotency_conflict": ("requisição idempotente conflitante", False),
    "lease_lost": ("posse do runtime perdida", False),
    "approval_denied": ("aprovação negada", False),
    "approval_stale": ("aprovação não está mais pendente", False),
    "cancel_partial": ("cancelamento ainda não confirmado", False),
    "transport": ("falha de transporte do runtime", True),
    "invalid_event": ("requisição de runtime inválida", False),
    "sequence_gap": ("sequência de eventos indisponível", True),
    "runtime_internal": ("falha interna do runtime", False),
}

# These are the only top-level fields journaled or exposed for each canonical
# event kind. Unknown kinds are retained with an empty payload, never raw data.
_TOP_LEVEL_FIELDS: dict[str, frozenset[str]] = {
    "text": frozenset({"threadId", "turnId", "itemId", "delta", "item", "replace"}),
    "reasoning": frozenset({"threadId", "turnId", "itemId", "delta", "summaryIndex", "partIndex"}),
    "tool": frozenset(
        {
            "threadId",
            "turnId",
            "itemId",
            "delta",
            "item",
            "nested",
            "startedAtMs",
            "completedAtMs",
        }
    ),
    "output": frozenset({"items", "text"}),
    "usage": frozenset({"threadId", "turnId", "tokenUsage"}),
    "error": frozenset({"will_retry"}),
    "turn": frozenset({"state"}),
    "snapshot": frozenset({"state", "items"}),
    "reconciled": frozenset({"snapshot", "reason"}),
    "turn_state": frozenset({"state"}),
    "turn_start": frozenset({"external_turn_id"}),
    "turn_end": frozenset({"state", "content", "usage"}),
    "dispatching": frozenset({"generation"}),
    "observation_attached": frozenset({"generation"}),
    "recovery": frozenset({"reason", "lease_generation"}),
    "approval": frozenset({"request_id", "rpc_request_id", "item_id", "request_kind", "details"}),
    "approval_request": frozenset(
        {
            "approval_id",
            "request_id",
            "rpc_request_id",
            "item_id",
            "request_kind",
            "details",
        }
    ),
    "approval_decision": frozenset({"approval_id", "decision", "delivery_state", "receipt"}),
    "approval_delivery": frozenset({"approval_id", "delivery_state"}),
    "approval_rejected": frozenset(
        {"approval_id", "requested_decision", "effective_decision", "code"}
    ),
    "approval_error": frozenset({"unsupported", "invalid"}),
}

# Nested values use a separate explicit vocabulary. It covers the pinned v2
# transcript, tool, usage, reconciliation and approval shapes used by Kairos.
_NESTED_FIELDS = frozenset(
    {
        "access",
        "action",
        "actionName",
        "activeFlags",
        "agentPath",
        "agentThreadId",
        "agentsStates",
        "aggregatedOutput",
        "appName",
        "appearance",
        "approval_id",
        "approvalId",
        "arguments",
        "audioUrl",
        "audio_url",
        "branch",
        "byteRange",
        "cachedInputTokens",
        "changes",
        "code",
        "command",
        "commandActions",
        "completedAt",
        "completedAtMs",
        "connectorId",
        "content",
        "contentItems",
        "createdAt",
        "custom",
        "cwd",
        "decision",
        "delivery",
        "delivery_state",
        "delta",
        "depth",
        "detail",
        "detailedExplanation",
        "details",
        "diff",
        "durationMs",
        "effective_decision",
        "enabled",
        "entries",
        "environmentId",
        "ephemeral",
        "execpolicy_amendment",
        "exitCode",
        "external_item_id",
        "external_request_id",
        "external_thread_id",
        "external_turn_id",
        "fileSystem",
        "files",
        "forkedFromId",
        "fragments",
        "generation",
        "gitInfo",
        "globScanMaxDepth",
        "grantRoot",
        "historyMode",
        "holder",
        "hookRunId",
        "host",
        "icon",
        "id",
        "imageUrl",
        "image_url",
        "inputTokens",
        "itemId",
        "item_id",
        "items",
        "kind",
        "last",
        "lease_generation",
        "lineEnd",
        "lineStart",
        "linkId",
        "mcpAppResourceUri",
        "memoryCitation",
        "model",
        "modelContextWindow",
        "modelProvider",
        "move_path",
        "name",
        "namespace",
        "network",
        "networkApprovalContext",
        "network_policy_amendment",
        "note",
        "ok",
        "options",
        "originUrl",
        "output",
        "outputTokens",
        "partIndex",
        "path",
        "pattern",
        "permissions",
        "phase",
        "placeholder",
        "pluginId",
        "process_generation",
        "processId",
        "progress",
        "prompt",
        "proposedExecpolicyAmendment",
        "proposedNetworkPolicyAmendments",
        "protocol",
        "queries",
        "query",
        "questions",
        "read",
        "readOnlyHint",
        "reason",
        "reasoningEffort",
        "reasoningOutputTokens",
        "receiverThreadIds",
        "recencyAt",
        "replace",
        "request",
        "request_id",
        "request_kind",
        "requested_decision",
        "resetsAt",
        "result",
        "results",
        "review",
        "revisedPrompt",
        "rpc_request_id",
        "savedPath",
        "scriptPath",
        "section",
        "sectionEnteredAt",
        "senderThreadId",
        "server",
        "session_id",
        "snapshot",
        "source",
        "start",
        "startedAt",
        "startedAtMs",
        "state",
        "status",
        "structuredContent",
        "subAgent",
        "subpath",
        "success",
        "summary",
        "summaryIndex",
        "text",
        "text_elements",
        "threadId",
        "threadIds",
        "threadSource",
        "thread_spawn",
        "title",
        "tokenUsage",
        "tool",
        "total",
        "totalTokens",
        "transparentBackground",
        "turnId",
        "turn_id",
        "turnKind",
        "turns",
        "type",
        "updatedAt",
        "url",
        "usage",
        "value",
        "write",
    }
)

_ARBITRARY_JSON_FIELDS = frozenset({"arguments", "structuredContent"})
_AGENT_STATE_FIELDS = frozenset({"message", "status"})


def public_error(code: str) -> dict[str, Any]:
    """Return one fixed message for a known code, or the internal fallback."""
    selected = code if code in RUNTIME_ERROR_CODES else "runtime_internal"
    message, retryable = _PUBLIC_ERRORS[selected]
    return {"code": selected, "message": message, "retryable": retryable}


def sanitize_payload(kind: str, payload: dict) -> dict[str, Any]:
    """Copy only fields approved for a canonical event kind.

    User and tool text in approved fields remains verbatim. This boundary does
    not claim to discover arbitrary secrets intentionally written in that text.
    """
    if not isinstance(kind, str) or not isinstance(payload, Mapping):
        return {}
    allowed = _TOP_LEVEL_FIELDS.get(kind, frozenset())
    return {
        key: _sanitize_value(value, field=key)
        for key, value in payload.items()
        if key in allowed and _safe_json_value(value)
    }


def _sanitize_value(value: Any, *, field: str | None = None) -> Any:
    if field in _ARBITRARY_JSON_FIELDS:
        return _copy_json(value)
    if field == "agentsStates":
        return _sanitize_agent_states(value)
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            key: _sanitize_value(item, field=key)
            for key, item in value.items()
            if isinstance(key, str) and key in _NESTED_FIELDS and _safe_json_value(item)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_value(item) for item in value if _safe_json_value(item)]
    if isinstance(value, (set, frozenset)):
        return [_sanitize_value(item) for item in sorted(value, key=repr) if _safe_json_value(item)]
    return None


def _sanitize_agent_states(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        agent_id: {
            key: _sanitize_value(item, field=key)
            for key, item in state.items()
            if isinstance(key, str) and key in _AGENT_STATE_FIELDS and _safe_json_value(item)
        }
        for agent_id, state in value.items()
        if isinstance(agent_id, str) and isinstance(state, Mapping)
    }


def _copy_json(value: Any) -> Any:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            key: _copy_json(item)
            for key, item in value.items()
            if isinstance(key, str) and _safe_json_value(item)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_copy_json(item) for item in value if _safe_json_value(item)]
    if isinstance(value, (set, frozenset)):
        return [_copy_json(item) for item in sorted(value, key=repr) if _safe_json_value(item)]
    return None


def _safe_json_value(value: Any) -> bool:
    if value is None or type(value) in {bool, int, str}:
        return True
    if type(value) is float:
        return math.isfinite(value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) for key in value)
    return isinstance(value, Sequence | set | frozenset) and not isinstance(
        value, (str, bytes, bytearray)
    )
