"""Read-only tool inventory, protected by the application's API middleware."""

from fastapi import APIRouter

from kairos_tools import registry

router = APIRouter()


@router.get("/api/tools/toolsets")
def list_toolsets():
    return registry.inventory()
