"""Cliente explícito do host compartilhado; nunca inicia processos."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .contracts import Decision, RuntimeEvent, Sandbox
from .errors import RuntimeErrorInfo
from .wire import MAX_MESSAGE_BYTES, encode_message, read_message

__all__ = ["RuntimeClient"]


class RuntimeClient:
    def __init__(self, socket_path: str | Path) -> None:
        self.socket_path = Path(socket_path)
        self._subscriptions: set[asyncio.StreamWriter] = set()
        self._closed = False

    async def create(
        self,
        cwd: str,
        sandbox: Sandbox,
        *,
        consent: bool = False,
        source: str = "web",
        parent_session_id: str | None = None,
        session_id: str | None = None,
    ) -> dict:
        return await self._call(
            "session.create",
            {
                "cwd": cwd,
                "sandbox": sandbox,
                "consent": consent,
                "source": source,
                "parent_session_id": parent_session_id,
                "session_id": session_id,
            },
        )

    async def get(self, session_id: str) -> dict:
        return await self._call("session.get", {"session_id": session_id})

    async def end(self, session_id: str) -> None:
        await self._call("session.end", {"session_id": session_id})

    async def submit(self, session_id: str, content: str, idempotency_key: str) -> str:
        return await self._call(
            "turn.submit",
            {
                "session_id": session_id,
                "content": content,
                "idempotency_key": idempotency_key,
            },
        )

    async def cancel(self, session_id: str, turn_id: str) -> None:
        await self._call("turn.cancel", {"session_id": session_id, "turn_id": turn_id})

    async def decide(self, session_id: str, approval_id: str, decision: Decision) -> None:
        await self._call(
            "approval.decide",
            {"session_id": session_id, "approval_id": approval_id, "decision": decision},
        )

    async def status(self) -> dict:
        return await self._call("runtime.status", {})

    async def account_status(self) -> dict:
        return await self._call("account.status", {})

    async def account_login(self, mode: str, api_key: str | None = None) -> dict:
        return await self._call("account.login", {"mode": mode, "api_key": api_key})

    async def account_cancel(self, login_id: str) -> None:
        await self._call("account.cancel", {"login_id": login_id})

    async def account_logout(self) -> None:
        await self._call("account.logout", {})

    async def subscribe(
        self, session_id: str, cursor: str | None = None
    ) -> AsyncIterator[RuntimeEvent]:
        if self._closed:
            raise RuntimeErrorInfo("unavailable", "cliente de runtime encerrado", False)
        reader, writer = await self._connect()
        self._subscriptions.add(writer)
        try:
            writer.write(
                encode_message(
                    {
                        "id": uuid.uuid4().hex,
                        "method": "events.subscribe",
                        "params": {"session_id": session_id, "cursor": cursor},
                    }
                )
            )
            await writer.drain()
            while not self._closed:
                message = await read_message(reader)
                error = message.get("error")
                if error is not None:
                    raise _decode_error(error)
                event = message.get("event")
                if not isinstance(event, dict):
                    raise RuntimeErrorInfo("invalid_event", "resposta de runtime inválida", False)
                yield RuntimeEvent.from_json(event)
        except (ConnectionError, OSError) as exc:
            raise RuntimeErrorInfo("unavailable", "host de runtime indisponível", True) from exc
        finally:
            self._subscriptions.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _call(self, method: str, params: dict[str, Any]) -> Any:
        if self._closed:
            raise RuntimeErrorInfo("unavailable", "cliente de runtime encerrado", False)
        try:
            reader, writer = await self._connect()
            try:
                request_id = uuid.uuid4().hex
                writer.write(encode_message({"id": request_id, "method": method, "params": params}))
                await writer.drain()
                response = await read_message(reader)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass
        except RuntimeErrorInfo:
            raise
        except (ConnectionError, OSError) as exc:
            raise RuntimeErrorInfo("unavailable", "host de runtime indisponível", True) from exc
        if response.get("id") != request_id:
            raise RuntimeErrorInfo("invalid_event", "resposta de runtime inválida", False)
        if "error" in response:
            raise _decode_error(response["error"])
        if set(response) != {"id", "result"}:
            raise RuntimeErrorInfo("invalid_event", "resposta de runtime inválida", False)
        return response["result"]

    async def _connect(self):
        return await asyncio.open_unix_connection(
            str(self.socket_path), limit=MAX_MESSAGE_BYTES + 2
        )

    async def aclose(self) -> None:
        self._closed = True
        writers = tuple(self._subscriptions)
        for writer in writers:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)


def _decode_error(value: object) -> RuntimeErrorInfo:
    if not isinstance(value, dict):
        return RuntimeErrorInfo("runtime_internal", "falha interna do runtime", False)
    code = value.get("code")
    message = value.get("message")
    retryable = value.get("retryable")
    try:
        return RuntimeErrorInfo(code, message, retryable)
    except (TypeError, ValueError):
        return RuntimeErrorInfo("runtime_internal", "falha interna do runtime", False)
