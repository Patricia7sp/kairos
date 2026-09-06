from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from contextlib import aclosing, suppress
from pathlib import Path

import pytest

from kairos_runtime import RuntimeClient, serve_runtime
from kairos_state import connect


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


async def _wait_turn_started(client: RuntimeClient, session_id: str, turn_id: str) -> None:
    async def wait() -> None:
        async with aclosing(client.subscribe(session_id)) as subscription:
            async for event in subscription:
                if event.turn_id == turn_id and event.kind == "turn_start":
                    return
        raise AssertionError("assinatura terminou antes de turn_start")

    await asyncio.wait_for(wait(), 180)


def _turn_window(home: Path, turn_id: str) -> tuple[tuple[float, float], tuple[str, ...]]:
    database = connect(home / "state.db")
    try:
        rows = database.execute(
            "SELECT kind,payload_json,created_at FROM runtime_events "
            "WHERE turn_id=? ORDER BY sequence",
            (turn_id,),
        ).fetchall()
    finally:
        database.close()
    starts = [float(row["created_at"]) for row in rows if row["kind"] == "turn_start"]
    ends = [float(row["created_at"]) for row in rows if row["kind"] == "turn_end"]
    assert len(starts) == len(ends) == 1
    states = tuple(
        str(json.loads(row["payload_json"])["state"]) for row in rows if row["kind"] == "turn_state"
    )
    return (starts[0], ends[0]), states


def _assert_directory_lease_owner(home: Path, turn_id: str) -> None:
    database = connect(home / "state.db")
    try:
        owner = database.execute(
            "SELECT turn_id FROM runtime_directory_leases WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    finally:
        database.close()
    assert owner is not None and owner["turn_id"] == turn_id


def _assert_live_schedule(
    *,
    winner: tuple[float, float],
    same_project: tuple[float, float],
    other_project: tuple[float, float],
    same_states: tuple[str, ...],
) -> None:
    winner_start, winner_end = winner
    same_start, _same_end = same_project
    other_start, other_end = other_project
    assert "queued" in same_states
    assert winner_start < winner_end <= same_start
    assert winner_start < other_end and other_start < winner_end


def test_live_timing_accepts_cross_project_overlap_and_same_project_queue():
    _assert_live_schedule(
        winner=(1.0, 4.0),
        same_project=(4.0, 6.0),
        other_project=(2.0, 3.0),
        same_states=("queued", "active", "completed"),
    )


@pytest.mark.parametrize(
    ("same_project", "other_project"),
    [
        ((3.0, 6.0), (2.0, 3.0)),
        ((4.0, 6.0), (4.0, 5.0)),
    ],
)
def test_live_timing_rejects_same_project_overlap_or_cross_project_serialization(
    same_project, other_project
):
    with pytest.raises(AssertionError):
        _assert_live_schedule(
            winner=(1.0, 4.0),
            same_project=same_project,
            other_project=other_project,
            same_states=("queued", "active", "completed"),
        )


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
        winner_turn = await client.submit(
            first["session_id"],
            "Write 300 to 350 words explaining why deterministic tests matter.",
            uuid.uuid4().hex,
        )
        winner_events = asyncio.create_task(
            _terminal_events(client, first["session_id"], winner_turn)
        )
        await _wait_turn_started(client, first["session_id"], winner_turn)
        _assert_directory_lease_owner(home, winner_turn)

        same_turn, other_turn = await asyncio.gather(
            client.submit(same_project["session_id"], "Reply with exactly A.", uuid.uuid4().hex),
            client.submit(other_project["session_id"], "Reply with exactly B.", uuid.uuid4().hex),
        )
        winner_result, same_events, other_events = await asyncio.gather(
            winner_events,
            _terminal_events(client, same_project["session_id"], same_turn),
            _terminal_events(client, other_project["session_id"], other_turn),
        )
        assert winner_result[-1].payload["state"] == "completed"
        assert same_events[-1].payload["state"] == "completed"
        assert other_events[-1].payload["state"] == "completed"
        winner_window, _winner_states = _turn_window(home, winner_turn)
        same_window, same_states = _turn_window(home, same_turn)
        other_window, _other_states = _turn_window(home, other_turn)
        _assert_live_schedule(
            winner=winner_window,
            same_project=same_window,
            other_project=other_window,
            same_states=same_states,
        )

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
