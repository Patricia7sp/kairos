import json
import os
import subprocess
import sys


def test_temporary_server_refuses_without_flag_before_listening(tmp_path):
    environment = {key: value for key, value in os.environ.items() if key != "KAIROS_E03_TEMPORARY"}
    environment["KAIROS_HOME"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "scripts.e03.serve", "--port", "0"],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_profile_required"
    assert not (tmp_path / "state.db").exists()


def test_temporary_server_rejects_production_home(tmp_path):
    environment = {**os.environ, "KAIROS_E03_TEMPORARY": "1", "KAIROS_HOME": "/opt/data"}
    result = subprocess.run(
        [sys.executable, "-m", "scripts.e03.serve", "--wait-profile-seconds", "0"],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_home_invalid"


def test_temporary_server_refuses_nonlocal_public_bind(tmp_path):
    environment = {**os.environ, "KAIROS_E03_TEMPORARY": "1", "KAIROS_HOME": str(tmp_path)}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.e03.serve",
            "--host",
            "8.8.8.8",
            "--wait-profile-seconds",
            "0",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 2
    assert json.loads(result.stderr)["error_kind"] == "temporary_arguments_invalid"
