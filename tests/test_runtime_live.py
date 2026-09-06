from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from contextlib import aclosing, suppress
from pathlib import Path

import pytest

from kairos_runtime import RuntimeClient, serve_runtime


async def _wait_ready(client: RuntimeClient, timeout: float = 30.0) -> dict:
    async def poll() -> dict:
        while True:
            try:
                status = await client.status()
            except Exception:  # noqa: BLE001 - bounded startup probe
                await asyncio.sleep(0.05)
                continue
            if status["state"] == "ready":
                return status
            await asyncio.sleep(0.05)

    return await asyncio.wait_for(poll(), timeout)


async def _terminal_events(
    client: RuntimeClient, session_id: str, turn_id: str, cursor: str | None = None
) -> list:
    async def collect() -> list:
        events = []
        async with aclosing(client.subscribe(session_id, cursor)) as subscription:
            async for event in subscription:
                if event.turn_id != turn_id:
                    continue
                events.append(event)
                if event.kind == "turn_end":
                    return events
        raise AssertionError("assinatura terminou sem turn_end")

    return await asyncio.wait_for(collect(), 180)


@pytest.mark.runtime_live
@pytest.mark.anyio
async def test_real_runtime_two_projects_queue_parallel_restart_and_logout(  # noqa: PLR0915
    tmp_path: Path,
):
    """Paid, explicit acceptance; default pytest and CI never collect this case."""
    if os.environ.get("KAIROS_RUNTIME_LIVE") != "1":
        pytest.skip("defina KAIROS_RUNTIME_LIVE=1 para o aceite real")
    api_key = os.environ.get("KAIROS_RUNTIME_LIVE_API_KEY")
    if not api_key:
        pytest.skip("aceite real exige KAIROS_RUNTIME_LIVE_API_KEY dedicada")
    executable = os.environ.get("KAIROS_RUNTIME_LIVE_CODEX") or shutil.which("codex")
    if not executable:
        pytest.skip("Codex 0.153.4 não encontrado")

    home = tmp_path / "home"
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    home.mkdir()
    project_a.mkdir()
    project_b.mkdir()
    (home / "config.yaml").write_text(
        "agent_runtime:\n"
        "  enabled: true\n"
        f"  codex_binary: {executable}\n"
        f"  allowed_directories: [{project_a}, {project_b}]\n"
        "  broad_access_enabled: false\n",
        encoding="utf-8",
    )

    host = asyncio.create_task(serve_runtime(home))
    client = RuntimeClient(home / "run" / "runtime.sock")
    logged_in = False
    try:
        status = await _wait_ready(client)
        assert status["authorized_projects"] == [str(project_a), str(project_b)]
        assert "broad_access" not in status["sandbox_profiles"]
        assert (await client.account_login("apiKey", api_key))["state"] == "succeeded"
        logged_in = True
        assert (await client.account_status())["authenticated"] is True

        first = await client.create(str(project_a), "read_only", source="acceptance")
        first_turn = await client.submit(
            first["session_id"], "Reply with exactly OK.", uuid.uuid4().hex
        )
        first_events = await _terminal_events(client, first["session_id"], first_turn)
        assert first_events[-1].payload["state"] == "completed"

        await client.aclose()
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)

        host = asyncio.create_task(serve_runtime(home))
        client = RuntimeClient(home / "run" / "runtime.sock")
        await _wait_ready(client)
        assert (await client.account_status())["authenticated"] is True
        second_turn = await client.submit(
            first["session_id"], "Reply with exactly OK again.", uuid.uuid4().hex
        )
        second_events = await _terminal_events(client, first["session_id"], second_turn)
        assert second_events[-1].payload["state"] == "completed"

        same_project = await client.create(str(project_a), "read_only", source="acceptance")
        other_project = await client.create(str(project_b), "read_only", source="acceptance")
        first_queued, same_turn, other_turn = await asyncio.gather(
            client.submit(first["session_id"], "Reply with exactly A1.", uuid.uuid4().hex),
            client.submit(same_project["session_id"], "Reply with exactly A.", uuid.uuid4().hex),
            client.submit(other_project["session_id"], "Reply with exactly B.", uuid.uuid4().hex),
        )
        first_queued_events, same_events, other_events = await asyncio.gather(
            _terminal_events(client, first["session_id"], first_queued),
            _terminal_events(client, same_project["session_id"], same_turn),
            _terminal_events(client, other_project["session_id"], other_turn),
        )
        assert first_queued_events[-1].payload["state"] == "completed"
        assert same_events[-1].payload["state"] == "completed"
        assert other_events[-1].payload["state"] == "completed"
        assert any(event.payload.get("state") == "queued" for event in same_events)

        await client.account_logout()
        logged_in = False
        assert (await client.account_status())["authenticated"] is False
    finally:
        if logged_in:
            with suppress(Exception):
                await client.account_logout()
        await client.aclose()
        host.cancel()
        await asyncio.gather(host, return_exceptions=True)
