"""The web inventory describes the actual registry without executing tools."""

import pytest
from fastapi.testclient import TestClient

from kairos_tools.builtin import register_builtin_tools
from kairos_tools.registry import ToolRegistry
from kairos_web import server


def test_inventory_reports_unavailable_tools_aliases_and_detached_schemas():
    registry = ToolRegistry()
    register_builtin_tools(registry)
    registry.register_toolset("offline", requirement=lambda: False)
    registry.register_toolset_alias("remote", "offline")
    registry.register(
        "remote_read",
        lambda: pytest.fail("inventory executed a tool"),
        {
            "type": "function",
            "function": {
                "name": "remote_read",
                "description": "Read the remote resource",
                "parameters": {"type": "object", "properties": {}},
            },
        },
        toolset="offline",
    )

    inventory = registry.inventory()

    assert inventory["total"] == 7
    assert inventory["available"] == 6
    tools = {tool["name"]: tool for tool in inventory["tools"]}
    assert tools["remote_read"]["available"] is False
    assert tools["remote_read"]["description"] == "Read the remote resource"
    assert tools["read_file"]["schema"]["function"]["parameters"]["required"] == ["path"]
    groups = {group["name"]: group for group in inventory["toolsets"]}
    assert groups["offline"] == {
        "name": "offline",
        "enabled": False,
        "aliases": ["remote"],
        "tools": ["remote_read"],
    }
    tools["read_file"]["schema"]["function"]["parameters"]["required"].clear()
    assert next(
        item for item in registry.get_definitions() if item["function"]["name"] == "read_file"
    )["function"]["parameters"]["required"] == ["path"]


def test_inventory_contains_live_registrations_and_requires_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    from kairos_tools.registry import registry

    name = "inventory_fixture_tool"
    registry.register(
        name,
        lambda: pytest.fail("inventory executed tool"),
        {"type": "function", "function": {"name": name, "description": "Fixture tool"}},
    )
    try:
        with TestClient(server.app) as client:
            assert client.get("/api/tools/toolsets").status_code == 401
            response = client.get(
                "/api/tools/toolsets", headers={server.TOKEN_HEADER: server.SESSION_TOKEN}
            )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == len(registry.get_all_tool_names())
        assert name in {tool["name"] for tool in data["tools"]}
        assert name in next(group for group in data["toolsets"] if group["name"] == "core")["tools"]
    finally:
        registry.unregister(name)


def test_registered_file_tools_execute_against_an_isolated_workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    registry = ToolRegistry()
    register_builtin_tools(registry)
    path = str(tmp_path / "notes" / "fixture.txt")

    assert registry.dispatch("write_file", {"path": path, "content": "alpha\nbeta\ngamma"})[
        "success"
    ]
    result = registry.dispatch("read_file", {"path": path, "offset": 1, "limit": 1})
    assert result["content"] == "beta"
    assert result["total_lines"] == 3
    assert registry.dispatch("edit_file", {"path": path, "old_str": "beta", "new_str": "updated"})[
        "success"
    ]
    assert registry.dispatch("read_file", {"path": path})["content"] == "alpha\nupdated\ngamma"
    assert registry.dispatch("list_dir", {"path": str(tmp_path / "notes")})["entries"] == [
        {"name": "fixture.txt", "is_dir": False, "size": 19}
    ]
