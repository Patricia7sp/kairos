"""Framing JSON-RPC seguro para o Codex App Server em stdio."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from .errors import RuntimeErrorInfo

__all__ = ["CodexRpc", "CodexSubscription"]


MAX_FRAME_BYTES = 1024 * 1024


def _transport() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("transport", "transporte do runtime indisponível", True)


@dataclass(eq=False)
class CodexSubscription:
    """Fila registrada antes de iniciar um turno para não perder mensagens adiantadas."""

    rpc: CodexRpc
    generation: str
    thread_id: str
    queue: asyncio.Queue[dict[str, Any] | RuntimeErrorInfo] = field(default_factory=asyncio.Queue)

    async def get(self) -> dict[str, Any]:
        message = await self.queue.get()
        if isinstance(message, RuntimeErrorInfo):
            raise message
        return message


class CodexRpc:
    """Um reader, escrita serializada e correlação exata de IDs JSON-RPC."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        generation: str,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        self.generation = generation
        self._reader = reader
        self._writer = writer
        self._max_frame_bytes = max_frame_bytes
        self._write_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._abandoned: set[int] = set()
        self._subscriptions: set[CodexSubscription] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._next_id = 1
        self._reader_task: asyncio.Task[None] | None = None
        self._closed_error: RuntimeErrorInfo | None = None

    async def start(self) -> None:
        if self._reader_task is None:
            self._reader_task = asyncio.create_task(self._read_messages())

    def subscribe(self, thread_id: str) -> CodexSubscription:
        if not thread_id:
            raise ValueError("thread_id é obrigatório")
        subscription = CodexSubscription(self, self.generation, thread_id)
        self._subscriptions.add(subscription)
        if self._closed_error is not None:
            subscription.queue.put_nowait(self._closed_error)
        return subscription

    def unsubscribe(self, subscription: CodexSubscription) -> None:
        self._subscriptions.discard(subscription)

    async def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._closed_error is not None:
            raise self._closed_error
        await self.start()
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        except asyncio.CancelledError:
            if self._pending.get(request_id) is future:
                self._abandoned.add(request_id)
            raise
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._closed_error is not None:
            raise self._closed_error
        message: dict[str, Any] = {"method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def reply(self, request_id: int | str, result: dict[str, Any]) -> None:
        self._validate_request_id(request_id)
        await self._write({"id": request_id, "result": result})

    async def reply_error(
        self, request_id: int | str, *, code: int = -32601, message: str = "unsupported request"
    ) -> None:
        self._validate_request_id(request_id)
        await self._write({"id": request_id, "error": {"code": code, "message": message}})

    async def aclose(self) -> None:
        self._fail(_transport())
        reader_task = self._reader_task
        if reader_task is not None and reader_task is not asyncio.current_task():
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks))
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _write(self, message: dict[str, Any]) -> None:
        encoded = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
        async with self._write_lock:
            if self._closed_error is not None:
                raise self._closed_error
            try:
                self._writer.write(encoded)
                await self._writer.drain()
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                error = _transport()
                self._fail(error)
                raise error from exc

    async def _read_messages(self) -> None:
        buffer = bytearray()
        try:
            while True:
                chunk = await self._reader.read(65536)
                if not chunk:
                    raise _transport()
                buffer.extend(chunk)
                if len(buffer) > self._max_frame_bytes and b"\n" not in buffer:
                    raise _transport()
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    if newline > self._max_frame_bytes:
                        raise _transport()
                    frame = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    self._dispatch(self._decode(frame))
        except asyncio.CancelledError:
            raise
        except (RuntimeErrorInfo, UnicodeDecodeError, json.JSONDecodeError, OSError) as exc:
            error = exc if isinstance(exc, RuntimeErrorInfo) else _transport()
            self._fail(error)

    @staticmethod
    def _decode(frame: bytes) -> dict[str, Any]:
        value = json.loads(frame.decode("utf-8"))
        if not isinstance(value, dict):
            raise _transport()
        return value

    def _dispatch(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            self._dispatch_response(message)
            return

        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            raise _transport()
        if "id" in message:
            self._validate_request_id(message["id"])
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            if "id" in message:
                self._schedule_rejection(message["id"])
            return
        copied = dict(message)
        matched = False
        for subscription in tuple(self._subscriptions):
            if subscription.thread_id == thread_id:
                matched = True
                subscription.queue.put_nowait(copied)
        if "id" in message and not matched:
            self._schedule_rejection(message["id"])

    def _dispatch_response(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        if type(request_id) is not int:
            raise _transport()
        future = self._pending.get(request_id)
        if future is None:
            if request_id in self._abandoned:
                self._abandoned.remove(request_id)
                return
            raise _transport()
        if future.cancelled():
            self._pending.pop(request_id, None)
            return
        if future.done():
            raise _transport()
        result = message.get("result")
        if "error" in message or not isinstance(result, dict):
            future.set_exception(_transport())
        else:
            future.set_result(result)

    def _schedule_rejection(self, request_id: int | str) -> None:
        task = asyncio.create_task(self._reject_unhandled(request_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _reject_unhandled(self, request_id: int | str) -> None:
        try:
            await self.reply_error(request_id)
        except RuntimeErrorInfo:
            pass

    def _fail(self, error: RuntimeErrorInfo) -> None:
        if self._closed_error is not None:
            return
        self._closed_error = error
        self._abandoned.clear()
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(error)
        for subscription in tuple(self._subscriptions):
            subscription.queue.put_nowait(error)

    @staticmethod
    def _validate_request_id(request_id: object) -> None:
        if type(request_id) not in {int, str}:
            raise _transport()
