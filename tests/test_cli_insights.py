"""The declared insights command reads real persisted usage without provider access."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kairos_cli.main import main
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository
from kairos_state.repositories.usage import BillingRoute, TokenDelta, UsageRepository


def _persist(home, *, unknown=False):
    db = connect(home / "state.db")
    try:
        initialize_schema(db)
        SessionRepository(db).create("private-session", "cli")
        usage = UsageRepository(db)
        usage.queue(
            BillingRoute("private-session", "private-model", "provider", "https://private", "api"),
            TokenDelta(
                api_call_count=2,
                input_tokens=7,
                output_tokens=3,
                actual_cost_usd=0.0,
                cost_status="actual",
            ),
        )
        if unknown:
            usage.queue(
                BillingRoute("private-session", "private-model", "other", "https://private", "api"),
                TokenDelta(api_call_count=1, estimated_cost_usd=0.2, cost_status="unknown"),
            )
        usage.flush(now=100)
    finally:
        db.close()


def test_cli_returns_persisted_usage_and_keeps_private_files_unchanged(
    tmp_path, monkeypatch, capsys
):
    _persist(tmp_path)
    (tmp_path / "config.yaml").write_text("private-secret: [invalid YAML")
    (tmp_path / "auth.json").write_text("private-secret invalid auth")
    before = {
        name: (tmp_path / name).read_bytes() for name in ("state.db", "config.yaml", "auth.json")
    }
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["insights", "--json"]) == 0
    output = capsys.readouterr().out
    data = json.loads(output)
    assert data["totals"]["requests"] == 2
    assert data["totals"]["tokens"] == 10
    assert data["totals"]["actual_cost_usd"] == 0
    assert data["totals"]["estimated_cost_usd"] is None
    assert data["period"] == "all_time"
    assert data["window_supported"] is False
    assert "private" not in output
    assert {name: (tmp_path / name).read_bytes() for name in before} == before


@pytest.mark.parametrize("state", ["absent", "corrupt", "empty"])
def test_cli_exit_status_distinguishes_empty_from_unavailable(tmp_path, monkeypatch, capsys, state):
    home = tmp_path / "home"
    if state != "absent":
        home.mkdir()
        if state == "empty":
            db = connect(home / "state.db")
            initialize_schema(db)
            db.close()
        else:
            (home / "state.db").write_bytes(b"private-secret-corrupt")
    monkeypatch.setenv("KAIROS_HOME", str(home))
    assert main(["insights", "--json"]) == (0 if state == "empty" else 1)
    result = capsys.readouterr()
    report = json.loads(result.out)
    assert report["availability"] == ("available" if state == "empty" else "unavailable")
    assert report["totals"]["actual_cost_usd"] is None
    assert "private" not in result.out + result.err
    if state == "absent":
        assert not home.exists()


def test_terminal_explains_accumulated_and_incomplete_costs(tmp_path, monkeypatch, capsys):
    _persist(tmp_path, unknown=True)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["insights"]) == 0
    result = capsys.readouterr().out.lower()
    assert "acumulado" in result
    assert "conhecid" in result
    assert "estimad" in result
    assert "incomplet" in result
    assert "private" not in result


def test_fresh_cli_process_is_profile_isolated_and_does_not_load_provider_or_web(tmp_path):
    active = tmp_path / "active"
    active.mkdir()
    _persist(active)
    other = tmp_path / "other"
    other.mkdir()
    _persist(other, unknown=True)
    env = {**os.environ, "KAIROS_HOME": str(active)}
    script = """
import sys
from kairos_cli.main import main
result = main(["insights", "--json"])
assert not any(name.startswith(("kairos_web", "fastapi", "httpx", "kairos_security.credentials")) for name in sys.modules)
raise SystemExit(result)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=10,
        cwd=Path(__file__).resolve().parents[1],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["totals"]["requests"] == 2
