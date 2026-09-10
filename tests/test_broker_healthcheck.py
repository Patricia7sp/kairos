from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

HEALTHCHECK = Path(__file__).parents[1] / "docker" / "broker" / "healthcheck.py"
WORKER_IMAGE = "sha256:" + "a" * 64
CONFIG = f"agent_runtime:\n  enabled: true\n  backend: docker\n  docker_image: {WORKER_IMAGE}\n"


async def _run_healthcheck(
    home: Path,
    docker_body: str | None = "raise SystemExit(0)",
    *,
    config: str | bytes | None = CONFIG,
    expected_image: str = WORKER_IMAGE,
) -> tuple[int, bytes, bytes]:
    if config is not None:
        (home / "config.yaml").write_bytes(config.encode() if isinstance(config, str) else config)
    env = os.environ.copy()
    env["KAIROS_HOME"] = str(home)
    bin_dir = home / "bin"
    bin_dir.mkdir()
    env["PATH"] = str(bin_dir)
    if docker_body is not None:
        docker = bin_dir / "docker"
        docker.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            f"assert sys.argv[1:] in [['info', '--format', '{{{{.ServerVersion}}}}'], ['image', 'inspect', '--', {expected_image!r}]]\n"
            "print('daemon-details-must-not-be-printed', flush=True)\n"
            "print('daemon-error-must-not-be-printed', file=sys.stderr, flush=True)\n"
            + docker_body
            + "\n"
        )
        docker.chmod(0o700)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(HEALTHCHECK),
        cwd=HEALTHCHECK.parents[2],
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout, stderr


async def _serve_response(
    socket_path: Path, response: Mapping[str, object] | None
) -> tuple[asyncio.AbstractServer, list[dict[str, object]]]:
    requests: list[dict[str, object]] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = json.loads(await reader.readline())
        requests.append(request)
        if response is None:
            await reader.read()
            return
        reply = {"id": request["id"], **response}
        writer.write(json.dumps(reply, separators=(",", ":")).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handle, path=socket_path)
    return server, requests


@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [
        ({"enabled": True, "state": "ready"}, 0),
        ({"enabled": False, "state": "disabled"}, 1),
        ({"enabled": True, "state": "unavailable"}, 1),
    ],
)
def test_healthcheck_exit_reflects_runtime_readiness_without_output(
    tmp_path: Path, status: dict[str, object], expected_exit: int
) -> None:
    async def run() -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        server, requests = await _serve_response(
            run_dir / "runtime.sock",
            {
                "result": {
                    **status,
                    "account": "must-not-be-printed",
                    "sessions": ["must-not-be-printed"],
                }
            },
        )
        try:
            exit_code, stdout, stderr = await _run_healthcheck(tmp_path)
        finally:
            server.close()
            await server.wait_closed()

        assert exit_code == expected_exit
        assert stdout == b""
        assert stderr == b""
        assert [(request["method"], request["params"]) for request in requests] == [
            ("runtime.status", {})
        ]

    asyncio.run(run())


@pytest.mark.parametrize("image_failure", ["raise SystemExit(1)", "import time; time.sleep(30)"])
def test_healthcheck_rejects_missing_or_unresponsive_configured_worker_image(
    tmp_path: Path, image_failure: str
) -> None:
    async def run() -> None:
        (tmp_path / "run").mkdir()
        server, _ = await _serve_response(
            tmp_path / "run/runtime.sock", {"result": {"enabled": True, "state": "ready"}}
        )
        # The daemon is reachable, but inspection of the configured image fails.
        body = "if sys.argv[1] == 'info': raise SystemExit(0)\n" + image_failure
        try:
            result = await asyncio.wait_for(_run_healthcheck(tmp_path, body), timeout=8)
        finally:
            server.close()
            await server.wait_closed()
        assert result == (1, b"", b"")

    asyncio.run(run())


@pytest.mark.parametrize(
    "config",
    [
        None,
        "[invalid",
        "[]",
        "agent_runtime: []",
        "agent_runtime: {enabled: false}",
        "agent_runtime: {enabled: true, backend: local}",
        "agent_runtime: {enabled: true, backend: docker, docker_image: --help}",
        "other: 2026-99-99",
        'agent_runtime: {enabled: true, backend: docker, allowed_directories: ["\\0"]}',
        b"\xff",
    ],
)
def test_healthcheck_rejects_invalid_or_inactive_docker_config_without_output(
    tmp_path: Path, config: str | bytes | None
) -> None:
    async def run() -> None:
        (tmp_path / "run").mkdir()
        server, _ = await _serve_response(
            tmp_path / "run/runtime.sock", {"result": {"enabled": True, "state": "ready"}}
        )
        try:
            result = await _run_healthcheck(tmp_path, config=config)
        finally:
            server.close()
            await server.wait_closed()
        assert result == (1, b"", b"")

    asyncio.run(run())


def test_healthcheck_inspects_default_worker_when_image_is_omitted(tmp_path: Path) -> None:
    async def run() -> None:
        (tmp_path / "run").mkdir()
        server, _ = await _serve_response(
            tmp_path / "run/runtime.sock", {"result": {"enabled": True, "state": "ready"}}
        )
        try:
            result = await _run_healthcheck(
                tmp_path,
                "raise SystemExit(0 if sys.argv[1] == 'image' else 1)",
                config="agent_runtime: {enabled: true, backend: docker}",
                expected_image="kairos:external-sandbox",
            )
        finally:
            server.close()
            await server.wait_closed()
        assert result == (0, b"", b"")

    asyncio.run(run())


@pytest.mark.parametrize(
    "docker_body", ["raise SystemExit(1)", "import time; time.sleep(30)", None]
)
def test_healthcheck_ready_runtime_requires_reachable_docker_without_output(
    tmp_path: Path, docker_body: str | None
) -> None:
    async def run() -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        server, _ = await _serve_response(
            run_dir / "runtime.sock", {"result": {"enabled": True, "state": "ready"}}
        )
        try:
            result = await asyncio.wait_for(_run_healthcheck(tmp_path, docker_body), timeout=8)
        finally:
            server.close()
            await server.wait_closed()
        assert result == (1, b"", b"")

    asyncio.run(run())


def test_healthcheck_runtime_error_is_unhealthy_without_output(tmp_path: Path) -> None:
    async def run() -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        server, requests = await _serve_response(
            run_dir / "runtime.sock",
            {
                "error": {
                    "code": "unavailable",
                    "message": "must-not-print-account-or-sessions",
                    "retryable": True,
                }
            },
        )
        try:
            exit_code, stdout, stderr = await _run_healthcheck(tmp_path)
        finally:
            server.close()
            await server.wait_closed()

        assert (exit_code, stdout, stderr) == (1, b"", b"")
        assert [(request["method"], request["params"]) for request in requests] == [
            ("runtime.status", {})
        ]

    asyncio.run(run())


def test_healthcheck_missing_socket_is_unhealthy_without_output(tmp_path: Path) -> None:
    (tmp_path / "run").mkdir()

    assert asyncio.run(_run_healthcheck(tmp_path)) == (1, b"", b"")


def test_healthcheck_timeout_is_unhealthy_without_output(tmp_path: Path) -> None:
    async def run() -> None:
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        server, requests = await _serve_response(run_dir / "runtime.sock", None)
        try:
            exit_code, stdout, stderr = await _run_healthcheck(tmp_path)
        finally:
            server.close()
            await server.wait_closed()

        assert (exit_code, stdout, stderr) == (1, b"", b"")
        assert [(request["method"], request["params"]) for request in requests] == [
            ("runtime.status", {})
        ]

    asyncio.run(run())
