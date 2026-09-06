"""Service-owned durable runtime execution, independent of UI subscriptions."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from .contracts import (
    AgentRuntimeProtocol,
    Decision,
    RuntimeEvent,
    RuntimeObservation,
    RuntimeSession,
    Sandbox,
)
from .errors import RuntimeErrorInfo
from .leases import RuntimeLeaseManager
from .policy import (
    DirectoryIdentity,
    authorize_directory,
    negotiate,
    revalidate_directory_identity,
    validate_sandbox,
)
from .recovery import TranscriptProjection, json_value, reconciliation
from .redaction import sanitize_payload
from .store import RuntimeStore


class AgentRuntimeService:
    def __init__(
        self,
        store: RuntimeStore,
        runtime: AgentRuntimeProtocol,
        *,
        allowed_directories: tuple[str, ...],
        broad_enabled: bool = False,
        policy_roots: Callable[[], tuple[str, ...]] | None = None,
        policy_broad: Callable[[], bool] | None = None,
        inactivity_confirmed: Callable[[str], bool] = lambda generation: False,
        leases: RuntimeLeaseManager | None = None,
        renew_interval: float = 10,
        cancel_timeout: float = 5,
        subscriber_backlog: int = 1024,
    ):
        self.store = store
        self.runtime = runtime
        self.leases = leases or RuntimeLeaseManager(store)
        self._roots = policy_roots or (lambda: allowed_directories)
        self._broad = policy_broad or (lambda: broad_enabled)
        self._inactive = inactivity_confirmed
        self._renew_interval = renew_interval
        self._cancel_timeout = cancel_timeout
        self._backlog = subscriber_backlog
        self._holder = uuid.uuid4().hex
        self._tasks: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._changed: dict[str, asyncio.Event] = {}
        self._closed = False
        self._owners: dict[str, int] = {}
        self._cancel_deadlines: dict[str, float] = {}
        self._dispatch_gate = asyncio.Event()
        self._dispatch_gate.set()
        self._dispatch_boundary = asyncio.Lock()
        self._losing: set[str] = set()
        self._loaded_threads: dict[str, str] = {}

    def _lock(self, session_id):
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _wake(self, session_id):
        self._changed.setdefault(session_id, asyncio.Event()).set()

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
        session_id = uuid.uuid4().hex if session_id is None else session_id
        if not isinstance(session_id, str) or not session_id.strip() or ":" in session_id:
            raise RuntimeErrorInfo("invalid_event", "identidade de sessão inválida", False)
        validate_sandbox(sandbox, self._broad(), consent)
        canonical = authorize_directory(cwd, self._roots())
        async with self._lock(session_id):
            try:
                previous = await self.get(session_id)
            except RuntimeErrorInfo as exc:
                if exc.code != "unavailable":
                    raise
            else:
                if (
                    previous["canonical_cwd"],
                    previous["sandbox_profile"],
                    previous["source"],
                    previous["parent_session_id"],
                    previous["broad_consent_at"] is not None,
                ) != (
                    canonical,
                    sandbox,
                    source,
                    parent_session_id,
                    sandbox == "broad_access" and consent,
                ):
                    raise RuntimeErrorInfo(
                        "idempotency_conflict", "identidade de sessão conflitante", False
                    )
                return previous
            session = RuntimeSession(session_id, "codex", cwd, sandbox)
            await self.store.create_session(
                session,
                source,
                parent_session_id,
                allowed_directories=self._roots(),
                broad_enabled=self._broad(),
                consent=consent,
            )
            # An unbound recovering row is durable evidence of an uncertain thread/start.
            await self.store.session_state(session_id, "recovering")
            try:
                caps = negotiate(await self.runtime.capabilities())
                await self._authorize(session_id)
                process_generation = self.runtime.generation
                thread = await self.runtime.create_thread(await self.store.get_session(session_id))
                await self.store.bind_thread(session_id, thread, caps)
                self._loaded_threads[session_id] = process_generation
            except BaseException:
                await self.store.session_state(session_id, "interrupted")
                raise
            return await self.get(session_id)

    async def get(self, session_id: str) -> dict:
        return await self.store.session_details(session_id)

    async def _authorize(self, session_id):
        details = await self.get(session_id)
        authorize_directory(details["requested_cwd"], self._roots())
        revalidate_directory_identity(
            DirectoryIdentity(
                details["requested_cwd"],
                details["canonical_cwd"],
                details["directory_device"],
                details["directory_inode"],
            )
        )
        validate_sandbox(
            details["sandbox_profile"], self._broad(), details["broad_consent_at"] is not None
        )
        return await self.store.get_session(session_id)

    async def submit(self, session_id: str, content: str, idempotency_key: str) -> str:
        if self._closed or not isinstance(content, str) or not content.strip():
            raise RuntimeErrorInfo("unavailable", "runtime indisponível", False)
        async with self._lock(session_id):
            # Admission checks an existing key before lifecycle state, including interrupted.
            await self._authorize(session_id)
            turn_id = await self.store.admit(session_id, idempotency_key, content)
            turn = await self.store.get_turn(turn_id)
            if turn["state"] == "queued" and turn_id not in self._tasks:
                await self.leases.enqueue(turn_id)
                await self.store.append(
                    turn_id, f"queued:{turn_id}", "turn_state", {"state": "queued"}
                )
                self._spawn(turn_id, self._execute(turn_id))
                self._wake(session_id)
            return turn_id

    def _spawn(self, turn_id, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks[turn_id] = task
        task.add_done_callback(lambda done: self._tasks.pop(turn_id, None))

    async def _owned(self, turn_id, generation, operation, *args):
        return await self.store.owned(turn_id, self._holder, generation, operation, *args)

    async def subscribe(
        self, session_id: str, cursor: str | None = None
    ) -> AsyncIterator[RuntimeEvent]:
        signal = self._changed.setdefault(session_id, asyncio.Event())
        replay = True
        while not self._closed:
            signal.clear()
            events = await self.store.events_after(session_id, cursor)
            if len(events) > self._backlog and not replay:
                raise RuntimeErrorInfo(
                    "sequence_gap", "assinante atrasado; retome pelo último cursor", True
                )
            replay = False
            for event in events:
                cursor = event.cursor
                yield event
            if not events:
                # Poll also covers another durable writer; notification only optimizes latency.
                try:
                    await asyncio.wait_for(signal.wait(), 0.2)
                except TimeoutError:
                    pass

    async def _execute(self, turn_id):  # noqa: PLR0912 - resume and dispatch fencing stay in one boundary
        turn = await self.store.get_turn(turn_id)
        generation = None
        heartbeat = None
        try:
            while generation is None:
                if (await self.store.get_turn(turn_id))["state"] != "queued":
                    return
                await self._dispatch_gate.wait()
                session = await self._authorize(turn["session_id"])
                generation = await self.leases.claim(turn_id, self._holder)
                if generation is None:
                    await asyncio.sleep(0.05)
            self._owners[turn_id] = generation
            heartbeat = asyncio.create_task(
                self._heartbeat(turn_id, generation, asyncio.current_task())
            )
            async with self._dispatch_slot():
                await self._authorize(session.session_id)
                if not await self.leases.renew(turn_id, self._holder, generation):
                    raise RuntimeErrorInfo("lease_lost", "lease de runtime perdida", False)
                process_generation = self.runtime.generation
                if self._loaded_threads.get(session.session_id) != process_generation:
                    try:
                        snapshot = await self.runtime.resume_thread(session)
                        if snapshot.state == "missing":
                            raise RuntimeErrorInfo(
                                "thread_missing", "thread de runtime ausente", False
                            )
                    except RuntimeErrorInfo as exc:
                        if exc.code == "thread_missing":
                            await self._owned(
                                turn_id,
                                generation,
                                "session_state",
                                session.session_id,
                                "unavailable",
                            )
                        raise
                    if self.runtime.generation != process_generation:
                        raise RuntimeErrorInfo("transport", "processo do runtime mudou", False)
                    if not await self.leases.renew(turn_id, self._holder, generation):
                        raise RuntimeErrorInfo("lease_lost", "lease de runtime perdida", False)
                    await self._authorize(session.session_id)
                    self._loaded_threads[session.session_id] = process_generation
                if not await self._owned(
                    turn_id, generation, "dispatch", turn_id, process_generation
                ):
                    return
                if self.runtime.generation != process_generation:
                    raise RuntimeErrorInfo("transport", "processo do runtime mudou", False)
                external = await self.runtime.start_turn(session, turn_id, turn["content"])
                await self._owned(turn_id, generation, "confirm_dispatch", turn_id, external)
            self._wake(session.session_id)
            await self._read(session, turn_id, generation)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - every failed execution must retain a durable uncertain outcome
            await self._finish_lost(turn_id)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    @asynccontextmanager
    async def _dispatch_slot(self):
        """Enter the start boundary only while the runtime generation is admitted."""
        while True:
            await self._dispatch_gate.wait()
            await self._dispatch_boundary.acquire()
            if self._dispatch_gate.is_set():
                break
            self._dispatch_boundary.release()
        try:
            yield
        finally:
            self._dispatch_boundary.release()

    async def pause_runtime(self, generation: str) -> None:
        """Close dispatch and quiesce every runner owned by one process generation."""
        if not isinstance(generation, str) or not generation:
            raise RuntimeErrorInfo("invalid_event", "geração de runtime inválida", False)
        self._dispatch_gate.clear()
        async with self._dispatch_boundary:
            runners = []
            for turn_id, task in tuple(self._tasks.items()):
                turn = await self.store.get_turn(turn_id)
                if turn["process_generation"] == generation:
                    runners.append((turn_id, task))
        for turn_id, task in runners:
            if not task.done() and turn_id not in self._losing:
                task.cancel()
        await asyncio.gather(*(task for _turn_id, task in runners), return_exceptions=True)

    def resume_runtime(self) -> None:
        """Reopen dispatch after the host has restarted and recovered the runtime."""
        if not self._closed:
            self._dispatch_gate.set()

    async def _finish_lost(self, turn_id: str) -> None:
        self._losing.add(turn_id)
        try:
            await self._lost(turn_id)
        finally:
            self._losing.discard(turn_id)

    async def _heartbeat(self, turn_id, generation, owner):
        while True:
            await asyncio.sleep(self._renew_interval)
            if not await self.leases.renew(turn_id, self._holder, generation):
                owner.cancel()
                return

    async def _projection(self, turn_id):
        turn = await self.store.get_turn(turn_id)
        projection = TranscriptProjection()
        for event in await self.store.events_after(turn["session_id"], None):
            if event.turn_id == turn_id:
                projection.apply(event)
        return projection

    async def _read(self, session, turn_id, lease_generation):
        observer = self.runtime.observe(session, turn_id)
        try:
            await self._consume(session, turn_id, lease_generation, observer)
        finally:
            close = getattr(observer, "aclose", None)
            if close is not None:
                await close()

    async def _consume(self, session, turn_id, lease_generation, observer):
        turn = await self.store.get_turn(turn_id)
        projection = await self._projection(turn_id)
        async for raw in observer:
            if (
                raw.get("generation") != self.runtime.generation
                or raw.get("generation") != turn["process_generation"]
                or raw.get("external_thread_id") != session.external_thread_id
                or raw.get("external_turn_id") != turn["external_turn_id"]
            ):
                raise RuntimeErrorInfo("invalid_event", "correlação de evento inválida", False)
            kind = raw["kind"]
            payload = sanitize_payload(kind, json_value(raw["payload"]))
            if kind == "turn" and payload["state"] != "active":
                await self._finish_observed(
                    session, turn_id, lease_generation, payload["state"], projection
                )
                return
            if kind == "approval":
                await self._owned(
                    turn_id,
                    lease_generation,
                    "save_approval",
                    turn_id,
                    raw["generation"],
                    payload["request_id"],
                    payload["item_id"],
                    payload,
                )
            else:
                event_id = raw.get("event_id") or uuid.uuid4().hex
                if kind == "snapshot":
                    observation = RuntimeObservation(
                        payload["state"], turn["external_turn_id"], tuple(payload["items"]), ()
                    )
                    event_id, payload = reconciliation(
                        session.external_thread_id, observation, "observation_lost"
                    )
                    kind = "reconciled"
                event = await self._owned(
                    turn_id, lease_generation, "append", turn_id, event_id, kind, payload
                )
                projection.apply(event)
                if raw["kind"] == "snapshot" and observation.state in {
                    "completed",
                    "failed",
                    "interrupted",
                    "cancelled",
                }:
                    await self._finish_observed(
                        session, turn_id, lease_generation, observation.state, projection
                    )
                    return
            self._wake(session.session_id)
        raise RuntimeErrorInfo("transport", "observação de runtime encerrada", True)

    async def _finish_observed(self, session, turn_id, lease_generation, state, projection):
        if state == "interrupted" and (await self.store.get_turn(turn_id))["state"] == "cancelling":
            state = "cancelled"
        await self._owned(
            turn_id,
            lease_generation,
            "finish",
            turn_id,
            state,
            projection.content,
            projection.usage,
        )
        await self.leases.release(turn_id, self._holder, lease_generation, confirmed_inactive=True)
        self._wake(session.session_id)

    async def _lost(self, turn_id):
        turn = await self.store.get_turn(turn_id)
        own_generation = self._owners.get(turn_id)
        if own_generation is not None and (
            turn["holder"] != self._holder or turn["lease_generation"] != own_generation
        ):
            return
        if turn["state"] in {"completed", "failed", "cancelled"}:
            return
        if turn["send_state"] == "not_sent":
            if turn["state"] == "queued":
                if not self._closed:
                    await self.leases.cancel_queued(turn_id)
                    self._wake(turn["session_id"])
                return
            # Durable not_sent proves no turn/start crossed the dispatch boundary.
            await self.store.finish(turn_id, "failed", "", None)
            if turn["holder"] == self._holder:
                await self.leases.release(
                    turn_id, self._holder, turn["lease_generation"], confirmed_inactive=True
                )
            self._wake(turn["session_id"])
            return
        projection = await self._projection(turn_id)
        if turn["holder"]:
            await self.store.lose_turn(
                turn_id,
                turn["holder"],
                turn["lease_generation"],
                projection.content,
                projection.usage,
            )
        else:
            await self.store.uncertain(turn_id)
            await self.store.finish(turn_id, "interrupted", projection.content, projection.usage)
        self._wake(turn["session_id"])
        if not self._closed:
            await self._inspect_loss(turn)

    async def _inspect_loss(self, turn):
        """Inspect a failed observation once; uncertain sends are never dispatched again."""
        deadline = self._cancel_deadlines.get(turn["id"])
        timeout = self._cancel_timeout
        if deadline is not None:
            timeout = min(timeout, deadline - asyncio.get_running_loop().time())
            if timeout <= 0:
                return
        try:
            session = await self._authorize(turn["session_id"])
            if deadline is not None:
                timeout = max(0, deadline - asyncio.get_running_loop().time())
            snapshot = await asyncio.wait_for(
                self.runtime.inspect_turn(session, turn["external_turn_id"]), timeout
            )
            if snapshot.state == "missing":
                raise RuntimeErrorInfo("thread_missing", "thread de runtime ausente", False)
            if (
                turn["external_turn_id"] is None
                or snapshot.external_turn_id != turn["external_turn_id"]
            ):
                return
            event_id, payload = reconciliation(
                session.external_thread_id, snapshot, "observation_lost"
            )
            await self.store.owned(
                turn["id"],
                turn["holder"],
                turn["lease_generation"],
                "append",
                turn["id"],
                event_id,
                "reconciled",
                payload,
                allow_expired=True,
            )
            projection = await self._projection(turn["id"])
            if (
                snapshot.state in {"completed", "failed", "cancelled", "interrupted"}
                and turn["process_generation"] == self.runtime.generation
            ):
                generation = await self.leases.adopt(
                    turn["id"],
                    turn["holder"],
                    turn["lease_generation"],
                    self._holder,
                    confirmed_inactive=True,
                )
                if generation is not None:
                    await self._owned(
                        turn["id"],
                        generation,
                        "finish",
                        turn["id"],
                        snapshot.state,
                        projection.content,
                        projection.usage,
                    )
                    await self.leases.release(
                        turn["id"], self._holder, generation, confirmed_inactive=True
                    )
            elif turn["holder"]:
                await self.store.lose_turn(
                    turn["id"],
                    turn["holder"],
                    turn["lease_generation"],
                    projection.content,
                    projection.usage,
                )
            self._wake(turn["session_id"])
        except RuntimeErrorInfo as exc:
            if exc.code == "thread_missing":
                await self.store.session_state(turn["session_id"], "unavailable")
        except TimeoutError:
            pass

    async def decide(self, session_id: str, approval_id: str, decision: Decision) -> None:
        async with self._lock(session_id):
            approval = await self.store.get_approval(approval_id)
            turn = await self.store.get_turn(approval["turn_id"])
            if turn["session_id"] != session_id or (
                approval["decision"] is not None and approval["decision"] != decision
            ):
                raise RuntimeErrorInfo("approval_stale", "aprovação não está mais pendente", False)
            if approval["decision"] == decision:
                return
            session = await self._authorize(session_id)
            request = approval["request"]
            details = request.get("details", {})
            if (
                approval["process_generation"] != self.runtime.generation
                or turn["state"] != "waiting_approval"
                or turn["holder"] != self._holder
                or details.get("threadId") != session.external_thread_id
                or details.get("turnId") != turn["external_turn_id"]
                or details.get("itemId") != approval["external_item_id"]
            ):
                raise RuntimeErrorInfo("approval_stale", "aprovação não está mais pendente", False)
            if decision == "accept" and session.sandbox != "broad_access":
                await self._owned(
                    turn["id"],
                    turn["lease_generation"],
                    "append",
                    turn["id"],
                    f"rejected:{approval_id}:accept",
                    "approval_rejected",
                    {
                        "approval_id": approval_id,
                        "requested_decision": "accept",
                        "effective_decision": None,
                        "code": "invalid_policy",
                    },
                )
                self._wake(session_id)
                raise RuntimeErrorInfo(
                    "invalid_policy", "a permissão exige nova sessão broad_access", False
                )
            if await self._owned(
                turn["id"], turn["lease_generation"], "decide_approval", approval_id, decision
            ):
                receipt = (await self.store.get_approval(approval_id))["decision_receipt"]
                self._wake(session_id)
                await self.runtime.respond_approval(approval["external_request_id"], decision)
                await self.store.acknowledge_approval(approval_id, receipt)
                try:
                    await self._owned(
                        turn["id"], turn["lease_generation"], "continue_after_approval", approval_id
                    )
                except RuntimeErrorInfo as exc:
                    if exc.code != "lease_lost":
                        raise
                    # Terminal release can win this race. Delivery is already durable;
                    # lifecycle changes must still obey the live execution fence.
                self._wake(session_id)

    async def cancel(self, session_id: str, turn_id: str) -> None:
        async with self._lock(session_id):
            turn = await self.store.get_turn(turn_id)
            if turn["session_id"] != session_id:
                raise RuntimeErrorInfo("invalid_event", "turno não pertence à sessão", False)
            if turn["state"] in {"completed", "failed", "cancelled"}:
                return
            if await self.leases.cancel_queued(turn_id):
                self._wake(session_id)
                return
            if turn["external_turn_id"] is None:
                task = self._tasks.get(turn_id)
                if task:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                await self._lost(turn_id)
                raise RuntimeErrorInfo("cancel_partial", "cancelamento ainda não confirmado", False)
            if (
                turn["holder"] != self._holder
                or turn["process_generation"] != self.runtime.generation
            ):
                raise RuntimeErrorInfo("cancel_partial", "cancelamento ainda não confirmado", False)
            try:
                await self._owned(
                    turn_id,
                    turn["lease_generation"],
                    "transition",
                    turn_id,
                    turn["state"],
                    "cancelling",
                )
            except RuntimeErrorInfo as exc:
                raise RuntimeErrorInfo(
                    "cancel_partial", "cancelamento ainda não confirmado", False
                ) from exc
            self._wake(session_id)
            await self._wait_cancellation(turn, send_interrupt=turn["state"] != "cancelling")
        turn = await self.store.get_turn(turn_id)
        if turn["state"] not in {"completed", "failed", "cancelled"}:
            raise RuntimeErrorInfo("cancel_partial", "cancelamento ainda não confirmado", False)

    async def _wait_cancellation(self, turn, *, send_interrupt):
        """One deadline covers RPC delivery, terminal evidence and bounded reconciliation."""
        turn_id = turn["id"]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._cancel_timeout
        self._cancel_deadlines[turn_id] = deadline
        runner = self._tasks.get(turn_id)
        interrupt = None
        try:
            if send_interrupt:
                session = await self.store.get_session(turn["session_id"])
                interrupt = asyncio.create_task(
                    self.runtime.cancel_turn(session, turn["external_turn_id"])
                )
            waiters = {task for task in (interrupt, runner) if task is not None}
            if waiters:
                done, _pending = await asyncio.wait(
                    waiters,
                    timeout=max(0, deadline - loop.time()),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if (
                    interrupt in done
                    and not interrupt.cancelled()
                    and interrupt.exception() is not None
                    and (runner is None or not runner.done())
                ):
                    await self._inspect_loss(turn)
                if runner is not None and not runner.done():
                    await asyncio.wait({runner}, timeout=max(0, deadline - loop.time()))
            if runner is None:
                await self._lost(turn_id)
        finally:
            # Cancelling the pending call marks its RPC id abandoned; a late response
            # is drained by CodexRpc without a retry or poisoning later calls.
            for task in (interrupt, runner):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (interrupt, runner) if task is not None), return_exceptions=True
            )
            self._cancel_deadlines.pop(turn_id, None)

    async def end(self, session_id: str) -> None:
        while True:
            async with self._lock(session_id):
                turns = [
                    turn
                    for turn in await self.store.nonterminal_turns()
                    if turn["session_id"] == session_id
                ]
                if not turns:
                    session = await self.store.get_session(session_id)
                    if (await self.get(session_id))["state"] == "ended":
                        return
                    if session.external_thread_id:
                        await self.runtime.end_thread(session)
                    await self.store.session_state(session_id, "ended")
                    self._wake(session_id)
                    return
            for turn in turns:
                await self.cancel(session_id, turn["id"])

    async def ensure_account_idle(self) -> None:
        """Refuse account mutation while any durable work may still execute."""
        if await self.store.nonterminal_turns():
            raise RuntimeErrorInfo("session_busy", "runtime possui trabalho pendente", True)

    async def recover(self) -> None:  # noqa: PLR0912 - explicit recovery branches preserve uncertainty boundaries
        for turn in await self.store.terminal_without_event():
            projection = await self._projection(turn["id"])
            await self.store.finish(turn["id"], turn["state"], projection.content, projection.usage)
            self._wake(turn["session_id"])
        for session in await self.store.unbound_sessions():
            await self.store.session_state(session["session_id"], "interrupted")
        for turn in await self.store.nonterminal_turns():
            if turn["id"] in self._tasks:
                continue
            try:
                session = await self._authorize(turn["session_id"])
                if turn["send_state"] == "not_sent" and turn["state"] == "queued":
                    await self.leases.enqueue(turn["id"])
                    self._spawn(turn["id"], self._execute(turn["id"]))
                    continue
                snapshot = await self.runtime.inspect_turn(session, turn["external_turn_id"])
                if snapshot.state == "missing":
                    raise RuntimeErrorInfo("thread_missing", "thread de runtime ausente", False)
                same_turn = (
                    turn["external_turn_id"] is not None
                    and snapshot.external_turn_id == turn["external_turn_id"]
                )
                if same_turn:
                    event_id, payload = reconciliation(
                        session.external_thread_id, snapshot, "observation_lost"
                    )
                    await self.store.owned(
                        turn["id"],
                        turn["holder"],
                        turn["lease_generation"],
                        "append",
                        turn["id"],
                        event_id,
                        "reconciled",
                        payload,
                        allow_expired=True,
                    )
                projection = await self._projection(turn["id"])
                inactive = bool(
                    turn["process_generation"] and self._inactive(turn["process_generation"])
                )
                if (
                    same_turn
                    and snapshot.state == "active"
                    and (inactive or turn["process_generation"] == self.runtime.generation)
                ):
                    await self._authorize(turn["session_id"])
                    generation = await self.leases.adopt(
                        turn["id"],
                        turn["holder"],
                        turn["lease_generation"],
                        self._holder,
                        confirmed_inactive=inactive,
                    )
                    if generation is not None:
                        self._owners[turn["id"]] = generation
                        self._spawn(
                            turn["id"],
                            self._resume(session, turn["id"], turn["external_turn_id"], generation),
                        )
                    else:
                        await self._lost(turn["id"])
                elif (
                    same_turn
                    and inactive
                    and snapshot.state in {"completed", "failed", "cancelled", "interrupted"}
                ):
                    generation = await self.leases.adopt(
                        turn["id"],
                        turn["holder"],
                        turn["lease_generation"],
                        self._holder,
                        confirmed_inactive=True,
                    )
                    if generation is not None:
                        await self._owned(
                            turn["id"],
                            generation,
                            "finish",
                            turn["id"],
                            snapshot.state,
                            projection.content,
                            projection.usage,
                        )
                        await self.leases.release(
                            turn["id"], self._holder, generation, confirmed_inactive=True
                        )
                else:
                    await self._lost(turn["id"])
            except RuntimeErrorInfo as exc:
                await self._lost(turn["id"])
                if exc.code == "thread_missing":
                    await self.store.session_state(turn["session_id"], "unavailable")
            self._wake(turn["session_id"])

    async def _resume(self, session, turn_id, external_turn_id, generation):
        heartbeat = asyncio.create_task(
            self._heartbeat(turn_id, generation, asyncio.current_task())
        )
        try:
            await self._authorize(session.session_id)
            process_generation = self.runtime.generation
            await self._owned(
                turn_id,
                generation,
                "append",
                turn_id,
                uuid.uuid4().hex,
                "observation_attached",
                {"generation": process_generation},
            )
            if self.runtime.generation != process_generation:
                raise RuntimeErrorInfo("transport", "processo do runtime mudou", False)
            await self.runtime.attach_turn(session, turn_id, external_turn_id)
            await self._read(session, turn_id, generation)
        except (Exception, asyncio.CancelledError):  # noqa: BLE001 - recovery failures preserve uncertainty, never resend
            await self._finish_lost(turn_id)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def aclose(self) -> None:
        self._closed = True
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.runtime.aclose()
        for signal in self._changed.values():
            signal.set()
