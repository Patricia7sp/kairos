"""Local reports observe real state and RPC without opening secrets or generating."""

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from kairos_cli.main import main
from kairos_observability.service_events import record_service_event
from kairos_state import SCHEMA_VERSION, connect, initialize_schema


def ready_home(home):
    home.mkdir(exist_ok=True)
    (home / "config.yaml").write_text("provider: openai\nmodel: gpt-4o\n")
    db = connect(home / "state.db")
    initialize_schema(db)
    db.close()
    record_service_event(home, "web.started")


@asynccontextmanager
async def runtime(home, result=None, *, stall=False, raw_response=None):
    directory = home / "run"
    directory.mkdir(exist_ok=True)
    closed = asyncio.Event()
    calls = []

    async def handle(reader, writer):
        try:
            message = json.loads(await reader.readline())
            calls.append(message["method"])
            if stall:
                await reader.read()
            else:
                response = raw_response or (
                    json.dumps({"id": message["id"], "result": result}).encode() + b"\n"
                )
                writer.write(response)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            closed.set()

    server = await asyncio.start_unix_server(handle, path=str(directory / "runtime.sock"))
    async with server:
        yield calls, closed


def test_complete_observation_does_not_mean_authentication_or_generation(tmp_path, monkeypatch):
    ready_home(tmp_path)
    for name in ("auth.json", "credentials.vault"):
        (tmp_path / name).write_text("private-secret")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}

    def forbidden(*_args, **_kwargs):
        pytest.fail("diagnostics must not open the vault or an HTTP client")

    monkeypatch.setattr("kairos_providers.composition.build_credential_service", forbidden)
    monkeypatch.setattr(
        "kairos_providers.composition._ProviderHttpClients._new_http_client", forbidden
    )

    async def scenario():
        from kairos_observability.diagnostics import read_diagnostics

        async with runtime(tmp_path, {"state": "ready", "enabled": True, "secret": "private"}) as (
            calls,
            _,
        ):
            report = await read_diagnostics(tmp_path)
        assert calls == ["runtime.status"]
        assert report["state"] == "complete" and report["scope"] == "local_only"
        assert report["provider"] == {
            "state": "available",
            "id": "openai",
            "authentication": "not_tested",
            "credentials": "not_inspected",
        }
        assert report["model"]["state"] == "available"
        assert report["model"]["generation"] == "not_tested"
        assert report["model"]["catalog_sources"] == ["curated"]
        assert report["database"]["schema_version"] == SCHEMA_VERSION
        assert report["database"]["integrity"] == "not_tested"
        assert report["events"] == {"state": "ready"}
        assert report["runtime"] == {"state": "ready", "enabled": True}
        assert report["privacy"]["data_collection"] == "deny"
        raw = json.dumps(report, allow_nan=False)
        assert "private" not in raw and str(tmp_path) not in raw and "gpt-4o" not in raw

    asyncio.run(scenario())
    for name, value in before.items():
        assert (tmp_path / name).read_bytes() == value


def test_missing_home_stays_missing_and_cli_returns_incomplete(tmp_path, monkeypatch, capsys):
    home = tmp_path / "absent"
    monkeypatch.setenv("KAIROS_HOME", str(home))
    assert main(["debug", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["state"] == "incomplete"
    assert report["configuration"]["state"] == "missing"
    assert report["database"]["state"] == "missing"
    assert report["runtime"]["state"] == "missing"
    assert not home.exists()


@pytest.mark.parametrize(
    "config,provider_state,model_state",
    [
        ("[broken", "unavailable", "unavailable"),
        ("provider: private-unknown\nmodel: private-model\n", "unknown", "unavailable"),
        ("provider: openai\nmodel: private-model\n", "available", "not_in_catalog"),
        ("provider: openai\n", "available", "not_configured"),
    ],
)
def test_configuration_failures_do_not_hide_other_sources(
    tmp_path, config, provider_state, model_state
):
    ready_home(tmp_path)
    (tmp_path / "config.yaml").write_text(config)
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["provider"]["state"] == provider_state
    assert report["model"]["state"] == model_state
    assert report["database"]["state"] == "available"
    assert report["events"]["state"] == "ready"
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "kind,state",
    [("corrupt", "unavailable"), ("old", "outdated"), ("incomplete", "schema_incomplete")],
)
def test_database_probes_do_not_repair_or_migrate(tmp_path, kind, state):
    path = tmp_path / "state.db"
    if kind == "corrupt":
        path.write_bytes(b"private-corrupt")
    else:
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE schema_version(version INTEGER)")
            db.execute(
                "INSERT INTO schema_version VALUES (?)", (1 if kind == "old" else SCHEMA_VERSION,)
            )
    before = path.read_bytes()
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["database"]["state"] == state
    assert path.read_bytes() == before
    assert "private" not in json.dumps(report)


@pytest.mark.parametrize(
    "result,expected",
    [
        ({"state": "disabled", "enabled": False}, "disabled"),
        ({"state": "private-invalid", "enabled": True}, "unavailable"),
        ({"state": "ready", "enabled": "private"}, "unavailable"),
        ("private-not-object", "unavailable"),
    ],
)
def test_runtime_response_is_projected_and_validated(tmp_path, result, expected):
    async def scenario():
        from kairos_observability.diagnostics import read_diagnostics

        async with runtime(tmp_path, result):
            report = await read_diagnostics(tmp_path)
        assert report["runtime"]["state"] == expected
        assert "private" not in json.dumps(report)

    asyncio.run(scenario())


def test_runtime_timeout_closes_actual_socket(tmp_path):
    async def scenario():
        from kairos_observability.diagnostics import read_diagnostics

        async with runtime(tmp_path, stall=True) as (calls, closed):
            report = await asyncio.wait_for(read_diagnostics(tmp_path), 5)
            await asyncio.wait_for(closed.wait(), 2)
        assert calls == ["runtime.status"]
        assert report["runtime"]["state"] == "unavailable"

    asyncio.run(scenario())


def test_runtime_cancellation_propagates_and_closes_socket(tmp_path):
    async def scenario():
        from kairos_observability.diagnostics import read_diagnostics

        async with runtime(tmp_path, stall=True) as (calls, closed):
            task = asyncio.create_task(read_diagnostics(tmp_path))
            async with asyncio.timeout(2):
                while not calls:
                    await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.wait_for(closed.wait(), 2)

    asyncio.run(scenario())


@pytest.mark.parametrize("stability", ["preview", "deprecated"])
def test_catalog_presence_does_not_override_selectability(tmp_path, monkeypatch, stability):
    from kairos_providers.contracts import ModelStability
    from kairos_providers.curated_catalog import curated_models

    ready_home(tmp_path)
    entries = tuple(
        replace(entry, stability=ModelStability(stability)) for entry in curated_models()
    )
    monkeypatch.setattr("kairos_providers.composition.curated_models", lambda: entries)
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["model"]["state"] == "not_selectable"
    assert report["model"]["generation"] == "not_tested"


def test_cli_in_fresh_process_observes_only_selected_home(tmp_path):
    selected = tmp_path / "selected"
    ready_home(selected)
    other = tmp_path / "other"
    other.mkdir()
    (other / "config.yaml").write_text("private: [broken")
    for as_json in (True, False):
        process = subprocess.run(
            [sys.executable, "-m", "kairos_cli.main", "debug", *(["--json"] if as_json else [])],
            env={**os.environ, "KAIROS_HOME": str(selected)},
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        assert process.returncode == 1  # No runtime in this home.
        assert not process.stderr
        assert "private" not in process.stdout and str(tmp_path) not in process.stdout
        if as_json:
            report = json.loads(process.stdout)
            assert report["configuration"]["state"] == "available"
            assert report["database"]["state"] == "available"
            assert report["runtime"]["state"] == "missing"
        else:
            assert "Autenticação e geração não testadas" in process.stdout
            assert "data_collection=deny" in process.stdout


def cached_home(home, *, tools=True, prompt=None):
    ready_home(home)
    (home / "config.yaml").write_text("provider: openai\nmodel: private-cached-model\n")
    (home / "model-catalog.json").write_text(
        json.dumps(
            {
                "snapshots": {
                    "openai": {
                        "fetched_at": 1,
                        "expires_at": 9999999999,
                        "models": [
                            {
                                "ref": {"provider": "openai", "model": "private-cached-model"},
                                "display_name": "private-display",
                                "capabilities": {"chat": True, "tools": tools},
                                "price": {"prompt": prompt},
                                "origins": ["dynamic"],
                            }
                        ],
                    }
                }
            }
        )
    )


@pytest.mark.parametrize("tools", ["private-secret", {"private": "secret"}, 1])
def test_invalid_cached_capability_is_not_disclosed_or_certified(tmp_path, tools):
    cached_home(tmp_path, tools=tools)
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["model"]["state"] == "unavailable"
    assert "tools" not in report["model"]
    assert "private" not in json.dumps(report)
    assert report["database"]["state"] == "available"


def test_invalid_cached_price_does_not_abort_independent_observations(tmp_path):
    cached_home(tmp_path, prompt="private-invalid-decimal")
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["state"] == "incomplete"
    assert report["model"]["state"] == "unavailable"
    assert report["database"]["state"] == "available"
    assert report["events"]["state"] == "ready"
    assert report["runtime"]["state"] == "missing"
    assert "private" not in json.dumps(report)


def test_deeply_nested_runtime_response_is_unavailable_and_socket_closes(tmp_path):
    async def scenario():
        from kairos_observability.diagnostics import read_diagnostics

        raw = b'{"result":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}\n"
        async with runtime(tmp_path, raw_response=raw) as (_, closed):
            report = await read_diagnostics(tmp_path)
            await asyncio.wait_for(closed.wait(), 2)
        assert report["runtime"] == {"state": "unavailable"}

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "config",
    [
        "model: openai/gpt-4o\n",
        "model: {provider: openai, model: gpt-4o}\n",
        "provider: ' openai '\nmodel: ' gpt-4o '\n",
    ],
)
def test_diagnostics_accepts_canonical_global_model_references(tmp_path, config):
    ready_home(tmp_path)
    (tmp_path / "config.yaml").write_text(config)
    from kairos_observability.diagnostics import read_diagnostics

    report = asyncio.run(read_diagnostics(tmp_path))
    assert report["provider"]["state"] == "available"
    assert report["provider"]["id"] == "openai"
    assert report["model"]["state"] == "available"
