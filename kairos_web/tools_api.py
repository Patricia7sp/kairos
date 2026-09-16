"""Read-only tool inventory, protected by the application's API middleware."""

from fastapi import APIRouter

from kairos_tools import registry

router = APIRouter()


@router.get("/api/tools/toolsets")
def list_toolsets():
    from kairos_integration.chat_tools import CHAT_TOOLS, MUTATING_TOOLS

    items = registry.inventory()
    for tool in items["tools"]:
        nome = tool["name"]
        tool["chat"] = nome in CHAT_TOOLS
        tool["mutating"] = nome in MUTATING_TOOLS
    return items
