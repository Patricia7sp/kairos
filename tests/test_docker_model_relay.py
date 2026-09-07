"""Untrusted worker protocol and real loopback bridge boundary."""

import asyncio
import base64
import http.client
import json
import sys
from pathlib import Path

import pytest

from kairos_runtime.docker_backend import model_relay as relay_module
from kairos_runtime.docker_backend.model_relay import ModelRelay


class Writer:
    def __init__(self):
        self.frames = []
        self.closed = False

    def write(self, data):
        self.frames.append(json.loads(data))

    async def drain(self):
        await asyncio.sleep(0)

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


def feed(reader, frame):
    reader.feed_data(json.dumps(frame).encode() + b"\n")


def request(request_id=1, **overrides):
    return {
        "type": "request",
        "id": request_id,
        "body": {"model": "test-model", "stream": True, **overrides},
    }


async def eventually(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


async def setup_relay(transport):
    reader, writer = asyncio.StreamReader(), Writer()
    relay = ModelRelay(reader, writer, transport, "test-model")
    feed(reader, {"type": "ready", "protocol": 1})
    await relay.start()
    return relay, reader, writer


def test_streams_only_active_turn_and_never_forwards_envelope_headers():
    async def run():
        seen = []

        async def transport(body):
            seen.append(body)
            yield b"data: first\n\n"
            yield b"data: [DONE]\n\n"

        relay, reader, writer = await setup_relay(transport)
        try:
            feed(reader, request())
            await eventually(lambda: writer.frames)
            assert writer.frames[-1]["status"] == 403
            assert not seen
            relay.allow_turn("turn-1")
            feed(reader, request(2))
            await eventually(lambda: any(f["type"] == "end" for f in writer.frames))
            assert seen == [request()["body"]]
            assert (
                b"".join(base64.b64decode(f["data"]) for f in writer.frames if f["type"] == "chunk")
                == b"data: first\n\ndata: [DONE]\n\n"
            )
            await relay.end_turn("turn-1")
            feed(reader, request(3))
            await eventually(lambda: writer.frames[-1]["id"] == 3)
            assert writer.frames[-1]["status"] == 403
        finally:
            await relay.aclose()
        assert writer.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": "other"},
        {"headers": {"Authorization": "secret"}},
        {"url": "https://evil.invalid"},
        {"stream": False},
        {"input": "x" * (2 * 1024 * 1024)},
        {"tools": [{"type": []}]},
        {"tools": None},
        {"tool_choice": {"type": "web_search"}},
    ],
)
def test_rejects_invalid_model_routes_headers_and_oversized_body(overrides):
    async def run():
        seen = []

        async def transport(body):
            seen.append(body)
            yield b"wrong"

        relay, reader, writer = await setup_relay(transport)
        try:
            relay.allow_turn("turn")
            feed(reader, request(**overrides))
            await eventually(lambda: writer.frames)
            assert writer.frames[-1]["type"] == "error"
            assert not seen
        finally:
            await relay.aclose()

    asyncio.run(run())


def test_cancellation_revokes_admission_and_closes_generator():
    async def run():
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def transport(body):
            try:
                entered.set()
                await asyncio.Event().wait()
                yield b"unreachable"
            finally:
                stopped.set()

        relay, reader, writer = await setup_relay(transport)
        relay.allow_turn("turn")
        feed(reader, request())
        await entered.wait()
        await relay.end_turn("turn")
        assert stopped.is_set()
        assert writer.frames[-1]["type"] == "error"
        feed(reader, request(2))
        await eventually(lambda: writer.frames[-1]["id"] == 2)
        assert writer.frames[-1]["status"] == 403
        await relay.aclose()

    asyncio.run(run())


def test_generic_transport_errors_response_bound_and_turn_budget(monkeypatch):
    async def run():
        count = 0

        async def transport(body):
            nonlocal count
            count += 1
            if count == 1:
                raise RuntimeError("Bearer SUPER_SECRET")
            yield b"x" * 9

        monkeypatch.setattr(relay_module, "MAX_RESPONSE_BYTES", 8)
        monkeypatch.setattr(relay_module, "MAX_REQUESTS_PER_TURN", 2)
        relay, reader, writer = await setup_relay(transport)
        try:
            relay.allow_turn("turn")
            for index in range(1, 4):
                feed(reader, request(index))
                await eventually(
                    lambda index=index: any(
                        f["id"] == index and f["type"] == "error" for f in writer.frames
                    )
                )
            assert writer.frames[-1]["status"] == 429
            assert count == 2
            assert "SUPER_SECRET" not in json.dumps(writer.frames)
            assert not any(f["type"] == "chunk" for f in writer.frames)
        finally:
            await relay.aclose()

    asyncio.run(run())


def test_real_bridge_http_stream_and_request_boundary():
    async def run():
        bridge = Path(__file__).resolve().parents[1] / "docker/external-sandbox/model_bridge.py"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-u",
            str(bridge),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        seen = []

        async def transport(body):
            seen.append(body)
            yield b'data: {"ok":true}\n\n'

        relay = ModelRelay(process.stdout, process.stdin, transport, "test-model")

        def http_request(path="/responses", method="POST", headers=None, body=None):
            connection = http.client.HTTPConnection("127.0.0.1", 8765, timeout=3)
            try:
                connection.request(
                    method,
                    path,
                    body=body or json.dumps(request()["body"]),
                    headers=headers
                    or {
                        "Content-Type": "application/json",
                        "Authorization": "Bearer WORKER_SECRET",
                        "ChatGPT-Account-ID": "injected",
                    },
                )
                response = connection.getresponse()
                return response.status, dict(response.getheaders()), response.read()
            finally:
                connection.close()

        try:
            await relay.start()
            assert (await asyncio.to_thread(http_request))[0] == 403
            relay.allow_turn("turn")
            status, headers, body = await asyncio.to_thread(http_request)
            assert status == 200
            assert headers["Content-Type"] == "text/event-stream"
            assert body == b'data: {"ok":true}\n\n'
            assert seen == [request()["body"]]
            for path, method in [
                ("/responses?url=evil", "POST"),
                ("/v1/responses", "POST"),
                ("/responses", "GET"),
                ("http://evil.invalid/responses", "POST"),
            ]:
                assert (await asyncio.to_thread(http_request, path, method))[0] in {400, 404, 405}
            assert (
                await asyncio.to_thread(
                    http_request, headers={"Content-Length": "2097153"}, body="x"
                )
            )[0] == 413
            assert (
                await asyncio.to_thread(
                    http_request,
                    headers={"Transfer-Encoding": "chunked", "Content-Length": "2"},
                    body="{}",
                )
            )[0] == 400
            assert (await asyncio.to_thread(http_request, body="[]"))[0] == 400
            assert len(seen) == 1
        finally:
            await relay.aclose()
            if process.returncode is None:
                process.terminate()
            await process.wait()

    asyncio.run(run())


@pytest.mark.parametrize(
    "payload",
    [b"[]\n", b"not json\n", b'{"type":"request","id":true}\n', b"x" * (2 * 1024 * 1024 + 8192)],
)
def test_invalid_frames_close_channel_without_transport(payload):
    async def run():
        async def transport(body):
            pytest.fail("invalid frame reached transport")
            yield b""

        relay, reader, writer = await setup_relay(transport)
        relay.allow_turn("turn")
        reader.feed_data(payload)
        await eventually(lambda: writer.closed)
        with pytest.raises(RuntimeError, match="unavailable"):
            relay.allow_turn("another")
        await relay.aclose()

    asyncio.run(run())


def test_request_timeout_completes_io_and_closes_transport(monkeypatch):
    async def run():
        stopped = asyncio.Event()

        async def transport(body):
            try:
                await asyncio.Event().wait()
                yield b""
            finally:
                stopped.set()

        monkeypatch.setattr(relay_module, "REQUEST_TIMEOUT", 0.02)
        relay, reader, writer = await setup_relay(transport)
        relay.allow_turn("turn")
        feed(reader, request())
        await eventually(lambda: writer.frames)
        assert stopped.is_set()
        assert writer.frames[-1]["status"] == 502
        await relay.aclose()

    asyncio.run(run())


def test_close_revokes_pending_stream_and_stale_turn_cannot_revoke_new_turn():
    async def run():
        entered, stopped = asyncio.Event(), asyncio.Event()

        async def transport(body):
            try:
                entered.set()
                yield b"data: started\n\n"
                await asyncio.Event().wait()
            finally:
                stopped.set()

        relay, reader, writer = await setup_relay(transport)
        relay.allow_turn("first")
        await relay.end_turn("first")
        relay.allow_turn("second")
        await relay.end_turn("first")
        feed(reader, request())
        await entered.wait()
        await relay.aclose()
        assert stopped.is_set()
        assert writer.closed
        assert writer.frames[-1]["type"] == "error"
        with pytest.raises(RuntimeError, match="unavailable"):
            relay.allow_turn("third")

    asyncio.run(run())


def test_cancelled_start_closes_pipe():
    async def run():
        async def transport(body):
            yield b""

        reader, writer = asyncio.StreamReader(), Writer()
        relay = ModelRelay(reader, writer, transport, "test-model")
        task = asyncio.create_task(relay.start())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert writer.closed

    asyncio.run(run())


def test_hosted_tools_cannot_enable_network_services():
    async def run():
        seen = []

        async def transport(body):
            seen.append(body)
            yield b"data: done\n\n"

        relay, reader, writer = await setup_relay(transport)
        try:
            relay.allow_turn("turn")
            for index, kind in enumerate(
                ["web_search", "web_search_preview", "image_generation", "code_interpreter", "mcp"],
                1,
            ):
                feed(reader, request(index, tools=[{"type": kind}]))
                await eventually(lambda index=index: any(f["id"] == index for f in writer.frames))
                assert writer.frames[-1]["status"] == 400
            assert not seen
            feed(
                reader,
                request(
                    6,
                    tools=[
                        {"type": "function", "name": "exec_command"},
                        {"type": "custom", "name": "apply_patch"},
                    ],
                ),
            )
            await eventually(lambda: any(f["type"] == "end" for f in writer.frames))
            assert len(seen) == 1
        finally:
            await relay.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("extra", [[{"type": "web_search"}], [{"type": "mcp"}], "invalid"])
@pytest.mark.parametrize("shape", ["field", "item"])
def test_responses_lite_cannot_smuggle_hosted_tools_in_input(extra, shape):
    async def run():
        seen = []

        async def transport(body):
            seen.append(body)
            yield b"data: done\n\n"

        relay, reader, writer = await setup_relay(transport)
        try:
            relay.allow_turn("turn")
            item = (
                {"role": "developer", "additional_tools": extra}
                if shape == "field"
                else {"type": "additional_tools", "role": "developer", "tools": extra}
            )
            feed(reader, request(input=[item]))
            await eventually(lambda: bool(writer.frames))
            assert writer.frames[0]["type"] == "error"
            assert writer.frames[0]["status"] == 400
            assert not seen
        finally:
            await relay.aclose()

    asyncio.run(run())
