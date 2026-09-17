"""Ferramentas: registro, política, orçamento e aprovação."""

from kairos_tools.approval import ApprovalContext, Decision, Layer, Verdict, resolve
from kairos_tools.budget import ResultBudget, SpilloverResult, maybe_spill
from kairos_tools.builtin import register_builtin_tools
from kairos_tools.git import register_git_tool
from kairos_tools.memory import MemoryStore, register_memory_tool
from kairos_tools.parallel import Segment, SegmentKind, ToolCall, segment_batch
from kairos_tools.policy import (
    DELEGATE_BLOCKED_TOOLS,
    SANDBOX_ALLOWED_TOOLS,
    delegate_block_reason,
    sandbox_allows,
)
from kairos_tools.registry import ToolEntry, ToolRegistry, registry, tool_error

__all__ = [
    "DELEGATE_BLOCKED_TOOLS",
    "SANDBOX_ALLOWED_TOOLS",
    "ApprovalContext",
    "Decision",
    "Layer",
    "MemoryStore",
    "ResultBudget",
    "Segment",
    "SegmentKind",
    "SpilloverResult",
    "ToolCall",
    "ToolEntry",
    "ToolRegistry",
    "Verdict",
    "delegate_block_reason",
    "maybe_spill",
    "register_builtin_tools",
    "register_git_tool",
    "register_memory_tool",
    "registry",
    "resolve",
    "sandbox_allows",
    "segment_batch",
    "tool_error",
]
