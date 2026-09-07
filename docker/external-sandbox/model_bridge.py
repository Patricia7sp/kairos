"""Standalone stdlib-only offline worker HTTP -> broker stdio bridge.

Installed read-only, run with /usr/local/bin/python -I -u. Wire protocol is
documented in kairos_runtime/docker_backend/model_relay.py. stdout is JSONL only.
"""

import base64
import binascii
import json
import os
import select
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PROTOCOL_VERSION = 1
BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 8765
MODEL_PATH = "/responses"
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = MAX_BODY_BYTES + 4096
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_CHUNK_BYTES = 64 * 1024
IO_TIMEOUT = 10.0
REQUEST_TIMEOUT = 120.0


class Bridge(HTTPServer):
    allow_reuse_address = True

    def __init__(self):
        super().__init__((BRIDGE_HOST, BRIDGE_PORT), Handler)
        self.sequence = 0
        self.buffer = bytearray()

    def receive(self, deadline):
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_FRAME_BYTES:
                raise ValueError("invalid frame")
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([sys.stdin.fileno()], [], [], remaining)[0]:
                raise TimeoutError
            data = os.read(sys.stdin.fileno(), MAX_CHUNK_BYTES)
            if not data:
                raise EOFError
            self.buffer.extend(data)
        line, _, rest = self.buffer.partition(b"\n")
        self.buffer = bytearray(rest)
        if len(line) > MAX_FRAME_BYTES:
            raise ValueError("invalid frame")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("invalid frame")
        return value

    def handle_error(self, request, client_address):
        # HTTPServer's default traceback may include request data; never print it.
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(IO_TIMEOUT)

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        data = b'{"error":"model request rejected"}'
        self.send_response_only(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.send_error(405)

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
    do_HEAD = do_GET
    do_OPTIONS = do_GET
    do_CONNECT = do_GET

    def do_POST(self):
        self.close_connection = True
        if self.path != MODEL_PATH:
            self.send_error(404)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            self.send_error(400)
            return
        if not lengths[0].isascii() or not lengths[0].isdigit():
            self.send_error(400)
            return
        if len(lengths[0]) > 10 or int(lengths[0]) > MAX_BODY_BYTES:
            self.send_error(413)
            return
        length = int(lengths[0])
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete body")
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("invalid body")
            self.server.sequence += 1
            request_id = self.server.sequence
            frame = (
                json.dumps(
                    {"type": "request", "id": request_id, "body": body},
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
                + b"\n"
            )
            if len(frame) - 1 > MAX_FRAME_BYTES:
                raise ValueError("invalid body")
        except (ValueError, RecursionError, OSError):
            self.send_error(400)
            return
        try:
            sys.stdout.buffer.write(frame)
            sys.stdout.buffer.flush()
            self._response(request_id)
        except (ValueError, RecursionError, OSError, EOFError, TimeoutError):
            self.send_error(502)

    def _response(self, request_id):
        deadline = time.monotonic() + REQUEST_TIMEOUT
        frame = self.server.receive(deadline)
        if frame.get("id") != request_id:
            raise ValueError("invalid response")
        if frame.get("type") == "error":
            self.send_error(frame["status"] if frame.get("status") in {400, 403, 429, 502} else 502)
            return True
        if frame != {"type": "start", "id": request_id, "status": 200}:
            raise ValueError("invalid response")
        self.send_response_only(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self._chunks(request_id, deadline)
        except (ValueError, binascii.Error, OSError, EOFError, TimeoutError):
            # Once SSE started, a truncated HTTP body makes failure visible to clients.
            pass
        return True

    def _chunks(self, request_id, deadline):
        total = 0
        while True:
            frame = self.server.receive(deadline)
            if frame.get("id") != request_id:
                raise ValueError("invalid response")
            if frame == {"type": "end", "id": request_id}:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
                return
            if set(frame) != {"type", "id", "data"} or frame["type"] != "chunk":
                raise ValueError("invalid response")
            if not isinstance(frame["data"], str) or len(frame["data"]) > 4 * (
                (MAX_CHUNK_BYTES + 2) // 3
            ):
                raise ValueError("invalid chunk")
            data = base64.b64decode(frame["data"], validate=True)
            total += len(data)
            if len(data) > MAX_CHUNK_BYTES or total > MAX_RESPONSE_BYTES:
                raise ValueError("response too large")
            if data:
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()


if __name__ == "__main__":
    with Bridge() as server:
        print(json.dumps({"type": "ready", "protocol": PROTOCOL_VERSION}), flush=True)
        server.serve_forever()
