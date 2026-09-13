"""Observe local readiness without inspecting credentials or contacting providers."""

from __future__ import annotations

import asyncio
import sqlite3
import stat
from decimal import InvalidOperation
from pathlib import Path

from kairos_integration.selection_context import parse_config_ref
from kairos_observability.service_events import read_service_events
from kairos_providers.adapters.openrouter import OpenRouterRoutingPolicy
from kairos_providers.catalog import UnknownModelError
from kairos_providers.composition import build_provider_gateway
from kairos_providers.settings import load_config_document
from kairos_runtime.client import RuntimeClient
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_state.connection import read_connection
from kairos_state.schema import SCHEMA_VERSION


async def _selection(home: Path) -> tuple[dict, dict, dict]:
    configuration = {"state": "unavailable"}
    provider = {
        "state": "unavailable",
        "authentication": "not_tested",
        "credentials": "not_inspected",
    }
    model = {"state": "unavailable", "generation": "not_tested"}
    gateway = None
    try:
        if not (home / "config.yaml").exists():
            configuration["state"] = "missing"
            return configuration, provider, model
        document = load_config_document(home)
        gateway = build_provider_gateway(home)
        configuration["state"] = "available"
        selection = parse_config_ref(document)
        selected_provider = selection.provider if selection else document.get("provider")
        selected_provider = (
            selected_provider.strip() if isinstance(selected_provider, str) else selected_provider
        )
        if selected_provider is None:
            provider["state"] = "not_configured"
        elif not isinstance(selected_provider, str) or selected_provider not in {
            descriptor.id for descriptor in gateway.registry.list_descriptors()
        }:
            provider["state"] = "unknown"
        else:
            provider.update(state="available", id=selected_provider)
            if selection is None:
                model["state"] = "not_configured"
            else:
                try:
                    entry = gateway.catalog.find(selection)
                except UnknownModelError:
                    model["state"] = "not_in_catalog"
                else:
                    # Cached dataclass annotations do not validate decoded JSON.
                    # Only project an observed boolean (or unknown), never raw data.
                    if (
                        entry.capabilities.tools is not None
                        and type(entry.capabilities.tools) is not bool
                    ):
                        return configuration, provider, model
                    model.update(
                        state="available" if entry.is_selectable() else "not_selectable",
                        catalog_sources=sorted(origin.value for origin in entry.origins),
                        tools=entry.capabilities.tools,
                    )
    except (OSError, ValueError, TypeError, RecursionError, InvalidOperation, OverflowError):
        # Never include raw configuration or exception strings in a shareable report.
        pass
    finally:
        if gateway is not None:
            await gateway.aclose()
    return configuration, provider, model


def _database(home: Path) -> dict:
    report = {"state": "unavailable", "integrity": "not_tested"}
    path = home / "state.db"
    try:
        if not path.exists():
            return {**report, "state": "missing"}
        if not path.is_file():
            return report
        with read_connection(path) as connection:
            versions = connection.execute("SELECT version FROM schema_version LIMIT 2").fetchall()
            if len(versions) != 1 or type(versions[0][0]) is not int:
                return report
            version = versions[0][0]
            # Project only bounded metadata, never arbitrary database values.
            if not 0 <= version <= 1_000_000:
                return report
            report["schema_version"] = version
            if version != SCHEMA_VERSION:
                return {**report, "state": "outdated" if version < SCHEMA_VERSION else "newer"}
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' "
                    "AND name IN ('sessions', 'messages', 'session_model_usage')"
                )
            }
            if tables != {"sessions", "messages", "session_model_usage"}:
                return {**report, "state": "schema_incomplete"}
            mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            if mode in {"wal", "delete", "truncate", "persist", "memory", "off"}:
                report["journal_mode"] = mode
            report["state"] = "available"
    except (OSError, sqlite3.Error):
        pass
    return report


async def _runtime(home: Path) -> dict:
    path = home / "run" / "runtime.sock"
    try:
        if not stat.S_ISSOCK(path.lstat().st_mode):
            return {"state": "unavailable"}
    except FileNotFoundError:
        return {"state": "missing"}
    except OSError:
        return {"state": "unavailable"}
    client = RuntimeClient(path)
    try:
        result = await asyncio.wait_for(client.status(), timeout=1)
        if isinstance(result, dict):
            state, enabled = result.get("state"), result.get("enabled")
            if (state == "ready" and enabled is True) or (state == "disabled" and enabled is False):
                return {"state": state, "enabled": enabled}
    except (OSError, RuntimeErrorInfo, TimeoutError, ValueError, TypeError, RecursionError):
        pass
    finally:
        await client.aclose()
    return {"state": "unavailable"}


async def read_diagnostics(home: Path) -> dict:
    """Report independent local observations; complete never certifies generation.

    Reads existing files only (SQLite may create WAL/SHM coordination files).
    The one-second deadline applies to the runtime RPC, not filesystem I/O.
    """
    configuration, provider, model = await _selection(home)
    database = _database(home)
    events = {"state": read_service_events(home, limit=1)["state"]}
    runtime = await _runtime(home)
    complete = (
        all(item["state"] == "available" for item in (configuration, provider, model, database))
        and events["state"] == "ready"
        and runtime["state"] in {"ready", "disabled"}
    )
    return {
        "scope": "local_only",
        "state": "complete" if complete else "incomplete",
        "configuration": configuration,
        "provider": provider,
        "model": model,
        "database": database,
        "events": events,
        "runtime": runtime,
        "privacy": {"scope": "application_default", **OpenRouterRoutingPolicy().as_payload()},
    }
