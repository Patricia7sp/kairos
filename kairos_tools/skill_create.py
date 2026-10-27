"""Criação gated por uma capacidade revogável do turno /learn."""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

SKILL_CREATE_DEFINITION = {
    "type": "function",
    "function": {
        "name": "skill_create",
        "description": "Propõe uma única skill nova. Exige revisão integral e confirmação humana; não cria sem aprovação.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "maxLength": 64},
                "skill_md": {"type": "string"},
            },
            "required": ["name", "skill_md"],
            "additionalProperties": False,
        },
    },
}


@dataclass
class SkillCreationCapability:
    publish: Callable[[str, str], Awaitable[dict[str, Any]]]
    closed: bool = False


_active: ContextVar[SkillCreationCapability | None] = ContextVar(
    "kairos-skill-create", default=None
)


@contextmanager
def skill_creation_scope(capability: SkillCreationCapability | None):
    token = _active.set(capability)
    try:
        yield
    finally:
        if capability is not None:
            capability.closed = True
        _active.reset(token)


def skill_creation_available() -> bool:
    holder = _active.get()
    return holder is not None and not holder.closed


async def skill_create_tool(name: str, skill_md: str) -> dict[str, Any]:
    holder = _active.get()
    if holder is None or holder.closed:
        return {"error": "Criação de skill não habilitada neste turno."}
    from kairos_tools.registry import _tools_allowed_under_test, _under_test

    if _under_test() and not _tools_allowed_under_test():
        return {"error": "Ferramenta bloqueada sob pytest."}
    return await holder.publish(name, skill_md)


def register_skill_create_tools(target) -> None:
    target.register_toolset("skill_authoring", requirement=skill_creation_available)
    target.register(
        name="skill_create",
        handler=skill_create_tool,
        schema=copy.deepcopy(SKILL_CREATE_DEFINITION),
        toolset="skill_authoring",
    )
