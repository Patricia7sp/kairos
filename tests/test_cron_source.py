"""Fonte de monitor: execução limitada, sem shell, sem segredos no ambiente."""

import json
import shlex
import sys

import pytest

from kairos_cron.source import MAX_SCRIPT_CHARS, run_script, validate_script


def cmd(*args) -> str:
    return " ".join(shlex.quote(arg) for arg in args)


def test_script_running_works_and_captures_stdout(tmp_path):
    result = run_script(cmd(sys.executable, "-c", "print('olá fonte')"), home=tmp_path)
    assert result.ok
    assert result.output == "olá fonte\n"


def test_stderr_is_appended_after_stdout(tmp_path):
    result = run_script(
        cmd(sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"),
        home=tmp_path,
    )
    assert result.ok
    assert result.output == "out\nerr\n"


def test_nonzero_exit_is_error_not_output(tmp_path):
    result = run_script(cmd(sys.executable, "-c", "raise SystemExit(3)"), home=tmp_path)
    assert not result.ok
    assert result.error == "failed"
    assert str(3) in result.detail


def test_missing_executable_is_unavailable(tmp_path):
    result = run_script("definitely-not-a-real-binary-xyz", home=tmp_path)
    assert not result.ok
    assert result.error == "unavailable"


def test_timeout_kills_process_group_and_reports_timeout(tmp_path):
    result = run_script(
        cmd(sys.executable, "-c", "import time; time.sleep(10)"), home=tmp_path, timeout=1
    )
    assert not result.ok
    assert result.error == "timeout"


def test_no_shell_means_pipe_characters_are_literal_arguments(tmp_path):
    result = run_script("echo um | dois", home=tmp_path)
    assert result.ok
    assert result.output == "um | dois\n"


def test_output_truncated_to_budget(tmp_path):
    result = run_script(
        cmd(sys.executable, "-c", "print('x' * 5000)"), home=tmp_path, max_output_chars=1024
    )
    assert result.ok
    assert len(result.output) <= 1024


def test_invalid_budget_values_are_rejected(tmp_path):
    assert run_script("echo x", home=tmp_path, max_output_chars=10).error == "invalid_script"
    assert run_script("echo x", home=tmp_path, timeout=0.5).error == "invalid_script"
    assert run_script("echo x", home=tmp_path, timeout=9999).error == "invalid_script"


def test_minimal_env_never_leaks_kairos_secrets(tmp_path):
    script = "import os,json,sys; print(json.dumps(sorted(os.environ), separators=(',',':')))"
    result = run_script(cmd(sys.executable, "-c", script), home=tmp_path)
    assert result.ok
    environ = json.loads(result.output)
    assert environ == sorted(["HOME", "PATH", "LANG", "LC_ALL", "TZ"])
    for leaked in ("KAIROS_HOME", "KAIROS_WEB_TOKEN", "VAULT_PASSPHRASE"):
        assert leaked not in environ


def test_validate_script_bounds_and_quotes():
    validate_script("echo x")
    with pytest.raises(ValueError):
        validate_script("")
    with pytest.raises(ValueError):
        validate_script("'aspas soltas")
    with pytest.raises(ValueError):
        validate_script("x" * (MAX_SCRIPT_CHARS + 1))
