"""Session-scoped offline workers with checkpoint-before-terminal publication.

Call recover() while holding the broker's exclusive host lock, before admitting
work. A broker generation is stable across worker replacements. Recorded workers
survive uncertain cleanup; that uncertainty closes admission until broker restart.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from kairos_providers._async_cleanup import run_persistent_cleanup

from ..codex_adapter import CodexAppServerAdapter
from ..contracts import Decision, RuntimeCapabilities, RuntimeObservation, RuntimeSession
from ..errors import RuntimeErrorInfo
from ..policy import RUNTIME_V1_FEATURES
from .registry import SessionRegistry

TERMINAL_STATES = frozenset({"completed", "failed", "interrupted", "cancelled"})


def _unavailable() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("unavailable", "runtime Docker indisponível", False)


class _SandboxAdapter(CodexAppServerAdapter):
    @staticmethod
    def _thread_policy_params(session: RuntimeSession) -> dict[str, Any]:
        return {
            "cwd": "/workspace",
            "sandbox": "read-only",
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
        }

    @staticmethod
    def _turn_sandbox_policy(session: RuntimeSession) -> dict[str, Any]:
        return {"type": "externalSandbox", "networkAccess": "restricted"}

    @classmethod
    def _validate_effective_policy(cls, result: dict, session: RuntimeSession) -> None:
        if (
            result.get("cwd") != "/workspace"
            or result.get("sandbox") != {"type": "readOnly", "networkAccess": False}
            or result.get("approvalPolicy") != "on-request"
            or result.get("approvalsReviewer") != "user"
            or result.get("modelProvider") != "kairos"
        ):
            raise RuntimeErrorInfo("invalid_policy", "política efetiva divergente", False)
        if session.external_thread_id is not None:
            cls._validate_thread(result, session.external_thread_id)

    @staticmethod
    def _validate_thread(result: dict, thread_id: str) -> None:
        thread = result.get("thread")
        if not isinstance(thread, dict) or thread.get("id") != thread_id:
            raise RuntimeErrorInfo("invalid_event", "correlação de thread inválida", False)

    async def inspect_turn(
        self, session: RuntimeSession, external_turn_id: str | None
    ) -> RuntimeObservation:
        thread_id = self._require_thread(session)
        result = await self._supervisor.rpc.call(
            "thread/read", {"threadId": thread_id, "includeTurns": True}
        )
        self._validate_thread(result, thread_id)
        return self._observation(result["thread"], external_turn_id)


@dataclass
class _Entry:
    session: RuntimeSession
    worker: Any
    adapter: _SandboxAdapter
    generation: str = ""
    turn_id: str | None = None
    external_turn_id: str | None = None
    closed: bool = False
    registered: bool = False
    finalized: bool = False
    finish_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    close_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DockerSessionRuntime:
    def __init__(
        self,
        registry: SessionRegistry,
        *,
        image: str,
        transport,
        model: str,
        max_workers: int = 2,
        worker_factory=None,
        worker_remover=None,
    ):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("max_workers must be positive")
        self.registry = registry
        self.image = image
        self.transport = transport
        self.model = model
        self._factory = worker_factory
        self._remover = worker_remover
        self._generation = uuid.uuid4().hex
        self._recovered = False
        self._blocked = False
        self._closed = False
        self._slots = asyncio.Semaphore(max_workers)
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._opening: set[asyncio.Task] = set()
        self._approvals: dict[str, _Entry] = {}
        self._snapshots: dict[tuple[str, str | None], RuntimeObservation] = {}
        self._close_lock = asyncio.Lock()

    @property
    def generation(self) -> str:
        return self._generation

    @property
    def ready(self) -> bool:
        return self._recovered and not self._blocked and not self._closed

    def attests_inactive(self, generation: str) -> bool:
        return bool(
            self._recovered
            and not self._blocked
            and generation
            and (generation != self.generation or not self._entries)
        )

    def _admit(self, session: RuntimeSession) -> None:
        if not self.ready:
            raise _unavailable()
        if session.sandbox not in {"read_only", "workspace_write"}:
            raise RuntimeErrorInfo("invalid_policy", "broad_access indisponível no Docker", False)
        try:
            self.registry.create(session)
        except (ValueError, OSError):
            raise RuntimeErrorInfo(
                "invalid_directory", "identidade da sessão divergente", False
            ) from None

    def _lock(self, session: RuntimeSession) -> asyncio.Lock:
        return self._locks.setdefault(session.session_id, asyncio.Lock())

    async def recover(self) -> None:
        if self._closed or self._entries:
            raise _unavailable()
        self._recovered = False
        try:
            if self._remover is None:
                from .worker import remove_recorded_worker

                self._remover = remove_recorded_worker
            for record in self.registry.list_workers():
                await self._remover(record.worker_name)
                self.registry.worker_removed(record.session_id)
        except BaseException:
            self._blocked = True
            raise
        self._blocked = False
        self._recovered = True

    async def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(1, RUNTIME_V1_FEATURES)

    async def _open(self, session: RuntimeSession, *, restore: bool) -> _Entry:
        self._admit(session)
        if session.session_id in self._entries:
            raise RuntimeErrorInfo("session_busy", "sessão Docker ocupada", True)
        entry = None
        reserved = False
        current = asyncio.current_task()
        self._opening.add(current)
        try:
            await self._slots.acquire()
            reserved = True
            self._admit(session)
            archives = self.registry.archives(session.session_id) if restore else None
            if restore and archives is None:
                raise RuntimeErrorInfo("thread_missing", "checkpoint de thread ausente", False)
            if self._factory is None:
                from .worker import SessionWorker

                self._factory = SessionWorker
            worker = self._factory(
                Path(session.cwd),
                image=self.image,
                writable=session.sandbox == "workspace_write",
                transport=self.transport,
                model=self.model,
                workspace_archive=archives[0] if archives else None,
                home_archive=archives[1] if archives else None,
            )
            entry = _Entry(replace(session, cwd="/workspace"), worker, _SandboxAdapter(worker))
            try:
                self.registry.record_worker(session.session_id, worker.name)
            except (ValueError, OSError):
                self._blocked = True
                raise
            entry.registered = True
            self._entries[session.session_id] = entry
            await worker.__aenter__()
            if not self.ready:
                raise _unavailable()
            entry.generation = worker.generation
            if not entry.generation or worker.rpc.generation != entry.generation:
                raise RuntimeErrorInfo("invalid_event", "geração de worker inválida", False)
            return entry
        except BaseException:
            if entry is not None:
                await self._discard(entry)
            elif reserved:
                self._slots.release()
            raise
        finally:
            self._opening.discard(current)

    async def _discard(self, entry: _Entry) -> None:
        outcome = await run_persistent_cleanup(
            lambda: self._close_entry(entry), task_name="docker-session-cleanup"
        )
        if outcome.error is not None:
            self._blocked = True
            raise _unavailable() from None
        if outcome.cancellation is not None:
            raise outcome.cancellation

    async def _close_entry(self, entry: _Entry) -> None:
        async with entry.close_lock:
            if entry.closed:
                return
            try:
                if entry.turn_id is not None:
                    await entry.worker.relay.end_turn(entry.turn_id)
            finally:
                await entry.worker.aclose()
            if entry.registered:
                self.registry.worker_removed(entry.session.session_id)
            entry.closed = True
            self._entries.pop(entry.session.session_id, None)
            self._approvals = {
                token: owner for token, owner in self._approvals.items() if owner is not entry
            }
            self._slots.release()

    async def _checkpoint(
        self, entry: _Entry, observation: RuntimeObservation | None = None
    ) -> None:
        async with entry.finish_lock:
            if entry.finalized:
                return
            try:
                if entry.turn_id is not None:
                    await entry.worker.relay.end_turn(entry.turn_id)
                workspace, home = await entry.worker.checkpoint()
                self.registry.checkpoint(
                    entry.session.session_id,
                    workspace,
                    home,
                    thread_id=entry.session.external_thread_id,
                )
                await self._discard(entry)
                entry.finalized = True
                if observation is not None:
                    self._snapshots[(entry.session.session_id, observation.external_turn_id)] = (
                        observation
                    )
            except BaseException:
                await self._discard(entry)
                raise

    async def create_thread(self, session: RuntimeSession) -> str:
        async with self._lock(session):
            self._admit(session)
            record = self.registry.get(session.session_id)
            if record.external_thread_id is not None:
                if self.registry.archives(session.session_id) is None:
                    raise _unavailable()
                return record.external_thread_id
            entry = await self._open(session, restore=False)
            try:
                thread_id = await entry.adapter.create_thread(entry.session)
                self.registry.bind_thread(session.session_id, thread_id)
                entry.session = replace(entry.session, external_thread_id=thread_id)
                await self._checkpoint(entry)
                return thread_id
            except BaseException:
                await self._discard(entry)
                raise

    async def resume_thread(self, session: RuntimeSession) -> RuntimeObservation:
        async with self._lock(session):
            self._admit(session)
            if session.session_id in self._entries:
                raise RuntimeErrorInfo("session_busy", "sessão Docker ocupada", True)
            entry = await self._open(session, restore=True)
            try:
                observation = await entry.adapter.resume_thread(entry.session)
                await self._checkpoint(entry)
                # A checkpoint can contain an abandoned active turn, never resume its execution.
                return (
                    replace(observation, state="unknown")
                    if observation.state == "active"
                    else observation
                )
            except BaseException:
                await self._discard(entry)
                raise

    async def start_turn(self, session: RuntimeSession, turn_id: str, content: str) -> str:
        async with self._lock(session):
            self._admit(session)
            entry = await self._open(session, restore=True)
            try:
                await entry.adapter.resume_thread(entry.session)
                entry.turn_id = turn_id
                entry.worker.relay.allow_turn(turn_id)
                entry.external_turn_id = await entry.adapter.start_turn(
                    entry.session, turn_id, content
                )
                return entry.external_turn_id
            except BaseException:
                await self._discard(entry)
                raise

    def _active(
        self,
        session: RuntimeSession,
        *,
        turn_id: str | None = None,
        external_turn_id: str | None = None,
    ) -> _Entry:
        self._admit(session)
        entry = self._entries.get(session.session_id)
        if (
            entry is None
            or entry.closed
            or (turn_id is not None and entry.turn_id != turn_id)
            or (external_turn_id is not None and entry.external_turn_id != external_turn_id)
        ):
            raise RuntimeErrorInfo("invalid_event", "turno Docker não está ativo", False)
        if (
            entry.session.external_thread_id != session.external_thread_id
            or entry.worker.generation != entry.generation
        ):
            raise RuntimeErrorInfo("invalid_event", "correlação de worker inválida", False)
        return entry

    async def observe(
        self, session: RuntimeSession, turn_id: str
    ) -> AsyncIterator[Mapping[str, Any]]:
        entry = self._active(session, turn_id=turn_id)
        try:
            async with aclosing(entry.adapter.observe(entry.session, turn_id)) as observer:
                async for event in observer:
                    if (
                        event.get("generation") != entry.generation
                        or event.get("external_thread_id") != entry.session.external_thread_id
                        or event.get("external_turn_id") != entry.external_turn_id
                    ):
                        raise RuntimeErrorInfo(
                            "invalid_event", "correlação de evento inválida", False
                        )
                    payload = event.get("payload", {})
                    if event.get("kind") == "approval":
                        self._approvals[payload["request_id"]] = entry
                    if (
                        event.get("kind") in {"turn", "snapshot"}
                        and payload.get("state") in TERMINAL_STATES
                    ):
                        observation = self._snapshots.get(
                            (session.session_id, entry.external_turn_id)
                        )
                        if not entry.finalized:
                            observation = await entry.adapter.inspect_turn(
                                entry.session, entry.external_turn_id
                            )
                            if (
                                observation.external_turn_id != entry.external_turn_id
                                or observation.state not in TERMINAL_STATES
                                or (
                                    observation.state != payload["state"]
                                    and {observation.state, payload["state"]}
                                    != {"interrupted", "cancelled"}
                                )
                            ):
                                raise RuntimeErrorInfo(
                                    "invalid_event", "snapshot terminal divergente", False
                                )
                        try:
                            await self._checkpoint(entry, observation)
                        except Exception:  # noqa: BLE001 — publish only generic checkpoint failures.
                            raise _unavailable() from None
                        yield {**event, "generation": self.generation}
                        return
                    yield {**event, "generation": self.generation}
        finally:
            if not entry.closed:
                await self._discard(entry)

    async def cancel_turn(self, session: RuntimeSession, external_turn_id: str) -> None:
        entry = self._active(session, external_turn_id=external_turn_id)
        await entry.worker.relay.end_turn(entry.turn_id)
        await entry.adapter.cancel_turn(entry.session, external_turn_id)

    async def respond_approval(self, request_id: str, decision: Decision) -> None:
        entry = self._approvals.pop(request_id, None)
        if entry is None or entry.closed or not self.ready:
            raise RuntimeErrorInfo("approval_stale", "aprovação não está mais pendente", False)
        await entry.adapter.respond_approval(request_id, decision)

    async def inspect_turn(
        self, session: RuntimeSession, external_turn_id: str | None
    ) -> RuntimeObservation:
        async with self._lock(session):
            self._admit(session)
            cached = self._snapshots.get((session.session_id, external_turn_id))
            if cached is not None:
                return cached
            entry = self._entries.get(session.session_id)
            temporary = entry is None
            if temporary:
                entry = await self._open(session, restore=True)
            elif external_turn_id is not None and entry.external_turn_id != external_turn_id:
                raise RuntimeErrorInfo("invalid_event", "correlação de turno inválida", False)
            try:
                if temporary:
                    await entry.adapter.resume_thread(entry.session)
                observation = await entry.adapter.inspect_turn(entry.session, external_turn_id)
                if temporary and observation.state == "active":
                    observation = replace(observation, state="unknown")
                if temporary or observation.state in TERMINAL_STATES:
                    await self._checkpoint(entry, observation)
                return observation
            except BaseException:
                if temporary:
                    await self._discard(entry)
                raise

    async def attach_turn(
        self, session: RuntimeSession, turn_id: str, external_turn_id: str
    ) -> RuntimeObservation:
        # Existing active binding already owns its subscription; no replay/resume dispatch.
        self._active(session, turn_id=turn_id, external_turn_id=external_turn_id)
        return await self.inspect_turn(session, external_turn_id)

    async def reconcile(self, session: RuntimeSession, cursor: str | None) -> RuntimeObservation:
        return await self.inspect_turn(session, None)

    async def end_thread(self, session: RuntimeSession) -> None:
        async with self._lock(session):
            self._admit(session)
            entry = self._entries.get(session.session_id)
            if entry is not None:
                if entry.turn_id is not None:
                    raise RuntimeErrorInfo("session_busy", "sessão Docker ocupada", True)
                await self._discard(entry)

    async def aclose(self) -> None:
        self._closed = True
        async with self._close_lock:
            opening = [task for task in self._opening if task is not asyncio.current_task()]
            for task in opening:
                task.cancel()
            await asyncio.gather(*opening, return_exceptions=True)
            errors = []
            for entry in tuple(self._entries.values()):
                try:
                    await self._discard(entry)
                except BaseException as exc:  # noqa: BLE001 — try every worker; retain failed ownership.
                    errors.append(exc)
            if errors:
                raise _unavailable() from None
