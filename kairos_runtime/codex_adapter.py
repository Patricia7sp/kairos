"""Adapter do contrato Kairos para a API v2 do Codex App Server."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any

from .codex_rpc import CodexRpc, CodexSubscription
from .contracts import Decision, RuntimeCapabilities, RuntimeObservation, RuntimeSession
from .errors import RuntimeErrorInfo
from .policy import RUNTIME_V1_FEATURES
from .supervisor import CodexSupervisor

__all__ = ["THREAD_SANDBOX", "CodexAppServerAdapter"]


THREAD_SANDBOX = {
    "read_only": "read-only",
    "workspace_write": "workspace-write",
    "broad_access": "danger-full-access",
}

APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
)


@dataclass(frozen=True)
class _PendingApproval:
    rpc: CodexRpc
    rpc_request_id: int | str
    method: str
    sandbox: str
    thread_id: str
    generation: str


class CodexAppServerAdapter:
    """Traduz threads, turnos e eventos sem repetir turnos após restart."""

    def __init__(self, supervisor: CodexSupervisor) -> None:
        self._supervisor = supervisor
        self._turn_subscriptions: dict[str, CodexSubscription] = {}
        self._external_turns: dict[str, str] = {}
        self._pending_approvals: dict[str, _PendingApproval] = {}
        self._cancelled: set[tuple[str, str]] = set()

    @property
    def generation(self) -> str:
        return self._supervisor.generation

    async def capabilities(self) -> RuntimeCapabilities:
        return RuntimeCapabilities(protocol_version=1, features=RUNTIME_V1_FEATURES)

    async def create_thread(self, session: RuntimeSession) -> str:
        params = self._thread_policy_params(session)
        result = await self._supervisor.rpc.call("thread/start", params)
        self._validate_effective_policy(result, session)
        thread = result.get("thread")
        if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
            raise self._invalid_event()
        return thread["id"]

    async def resume_thread(self, session: RuntimeSession) -> RuntimeObservation:
        thread_id = self._require_thread(session)
        params = {
            "threadId": thread_id,
            "excludeTurns": False,
            **self._thread_policy_params(session),
        }
        result = await self._supervisor.rpc.call("thread/resume", params)
        self._validate_effective_policy(result, session)
        return self._observation(result.get("thread"), requested_turn_id=None)

    async def start_turn(self, session: RuntimeSession, turn_id: str, content: str) -> str:
        thread_id = self._require_thread(session)
        if not turn_id or not content:
            raise self._invalid_event()
        if turn_id in self._turn_subscriptions:
            raise RuntimeErrorInfo("session_busy", "turno já está ativo", False)
        rpc = self._supervisor.rpc
        subscription = rpc.subscribe(thread_id)
        self._turn_subscriptions[turn_id] = subscription
        try:
            result = await rpc.call(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": content}],
                    "clientUserMessageId": turn_id,
                    "cwd": session.cwd,
                    "approvalPolicy": "on-request",
                    "approvalsReviewer": "user",
                    "sandboxPolicy": self._turn_sandbox_policy(session),
                },
            )
            turn = result.get("turn")
            external_turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(external_turn_id, str) or not external_turn_id:
                raise self._invalid_event()
            self._external_turns[turn_id] = external_turn_id
            return external_turn_id
        except BaseException:
            subscription.rpc.unsubscribe(subscription)
            self._turn_subscriptions.pop(turn_id, None)
            raise

    async def observe(
        self, session: RuntimeSession, turn_id: str
    ) -> AsyncIterator[Mapping[str, Any]]:
        subscription = self._turn_subscriptions.get(turn_id)
        if subscription is None:
            raise self._invalid_event()
        external_turn_id = self._external_turns.get(turn_id)
        try:
            while True:
                message = await subscription.get()
                message_turn_id = self._message_turn_id(message)
                if "id" in message and message.get("method") not in APPROVAL_METHODS:
                    event = await self._translate(message, session, subscription)
                    if event is not None:
                        yield event
                    continue
                if external_turn_id is not None and message_turn_id != external_turn_id:
                    continue
                event = await self._translate(message, session, subscription)
                if event is None:
                    continue
                yield event
                if event["kind"] == "turn" and event["payload"]["state"] in {
                    "completed",
                    "failed",
                    "interrupted",
                }:
                    return
        finally:
            subscription.rpc.unsubscribe(subscription)
            self._turn_subscriptions.pop(turn_id, None)

    async def cancel_turn(self, session: RuntimeSession, external_turn_id: str) -> None:
        thread_id = self._require_thread(session)
        await self._supervisor.rpc.call(
            "turn/interrupt", {"threadId": thread_id, "turnId": external_turn_id}
        )
        self._cancelled.add((thread_id, external_turn_id))

    async def respond_approval(self, request_id: str, decision: Decision) -> None:
        pending = self._pending_approvals.pop(request_id, None)
        if pending is None:
            raise RuntimeErrorInfo("approval_stale", "aprovação não está mais pendente", False)
        try:
            current_rpc = self._supervisor.rpc
        except RuntimeErrorInfo as exc:
            raise RuntimeErrorInfo(
                "approval_stale", "aprovação pertence a um processo encerrado", False
            ) from exc
        if pending.rpc is not current_rpc or pending.generation != current_rpc.generation:
            raise RuntimeErrorInfo(
                "approval_stale", "aprovação pertence a um processo encerrado", False
            )
        if decision == "accept" and pending.sandbox != "broad_access":
            await pending.rpc.reply(pending.rpc_request_id, {"decision": "decline"})
            raise RuntimeErrorInfo(
                "invalid_policy",
                "a permissão exige uma nova sessão broad_access com consentimento",
                False,
            )
        await pending.rpc.reply(
            pending.rpc_request_id,
            {"decision": "accept" if decision == "accept" else "decline"},
        )

    async def inspect_turn(
        self, session: RuntimeSession, external_turn_id: str | None
    ) -> RuntimeObservation:
        result = await self._supervisor.rpc.call(
            "thread/read",
            {"threadId": self._require_thread(session), "includeTurns": True},
        )
        return self._observation(result.get("thread"), requested_turn_id=external_turn_id)

    async def reconcile(self, session: RuntimeSession, cursor: str | None) -> RuntimeObservation:
        del cursor  # thread/read oferece snapshot, não cursor de replay.
        return await self.inspect_turn(session, None)

    async def end_thread(self, session: RuntimeSession) -> None:
        result = await self._supervisor.rpc.call(
            "thread/unsubscribe", {"threadId": self._require_thread(session)}
        )
        if result.get("status") not in {"notLoaded", "notSubscribed", "unsubscribed"}:
            raise self._invalid_event()

    async def aclose(self) -> None:
        for subscription in tuple(self._turn_subscriptions.values()):
            subscription.rpc.unsubscribe(subscription)
        self._turn_subscriptions.clear()
        await self._supervisor.aclose()

    async def _translate(
        self,
        message: dict[str, Any],
        session: RuntimeSession,
        subscription: CodexSubscription,
    ) -> dict[str, Any] | None:
        method = message["method"]
        params = message["params"]
        base = {
            "generation": subscription.generation,
            "external_thread_id": params.get("threadId"),
            "external_turn_id": self._message_turn_id(message),
        }
        if "id" in message:
            if method not in APPROVAL_METHODS:
                await subscription.rpc.reply_error(message["id"])
                return {**base, "kind": "approval_error", "payload": {"unsupported": True}}
            if not self._valid_approval(params):
                await subscription.rpc.reply_error(
                    message["id"], code=-32602, message="invalid approval request"
                )
                return {**base, "kind": "approval_error", "payload": {"invalid": True}}
            token = self._approval_token(subscription.generation, message["id"])
            self._pending_approvals[token] = _PendingApproval(
                rpc=subscription.rpc,
                rpc_request_id=message["id"],
                method=method,
                sandbox=session.sandbox,
                thread_id=params["threadId"],
                generation=subscription.generation,
            )
            return {
                **base,
                "kind": "approval",
                "payload": {
                    "request_id": token,
                    "rpc_request_id": message["id"],
                    "item_id": params["itemId"],
                    "request_kind": method,
                    "details": dict(params),
                },
            }
        if method == "item/agentMessage/delta":
            return {**base, "kind": "text", "payload": dict(params)}
        if method in {
            "item/reasoning/textDelta",
            "item/reasoning/summaryTextDelta",
            "item/reasoning/summaryPartAdded",
        }:
            return {**base, "kind": "reasoning", "payload": dict(params)}
        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") == "agentMessage":
                return {
                    **base,
                    "kind": "text",
                    "payload": {"item": dict(item), "replace": method == "item/completed"},
                }
            return {**base, "kind": "tool", "payload": dict(params)}
        if method == "thread/tokenUsage/updated":
            return {**base, "kind": "usage", "payload": dict(params)}
        if method == "error":
            return {
                **base,
                "kind": "error",
                "payload": {"will_retry": params.get("willRetry") is True},
            }
        if method in {"turn/started", "turn/completed"}:
            turn = params.get("turn")
            if not isinstance(turn, dict):
                raise self._invalid_event()
            return {
                **base,
                "kind": "turn",
                "payload": {"state": self._turn_state(turn.get("status"))},
            }
        return None

    @staticmethod
    def _approval_token(generation: str, request_id: int | str) -> str:
        prefix = "integer" if type(request_id) is int else "string"
        return f"{generation}:{prefix}:{request_id}"

    @staticmethod
    def _valid_approval(params: object) -> bool:
        if not isinstance(params, dict):
            return False
        return (
            isinstance(params.get("itemId"), str)
            and type(params.get("startedAtMs")) is int
            and isinstance(params.get("threadId"), str)
            and isinstance(params.get("turnId"), str)
        )

    @staticmethod
    def _message_turn_id(message: Mapping[str, Any]) -> str | None:
        params = message.get("params")
        if not isinstance(params, Mapping):
            return None
        turn = params.get("turn")
        candidate = turn.get("id") if isinstance(turn, Mapping) else params.get("turnId")
        return candidate if isinstance(candidate, str) else None

    def _observation(self, thread: object, requested_turn_id: str | None) -> RuntimeObservation:
        if not isinstance(thread, dict):
            raise self._invalid_event()
        turns = thread.get("turns")
        if not isinstance(turns, list):
            raise self._invalid_event()
        selected: dict[str, Any] | None = None
        for turn in reversed(turns):
            if isinstance(turn, dict) and (
                requested_turn_id is None or turn.get("id") == requested_turn_id
            ):
                selected = turn
                break
        if selected is None:
            return RuntimeObservation(
                "unknown", None, (), self._pending_for_thread(thread.get("id"))
            )
        turn_id = selected.get("id")
        if not isinstance(turn_id, str):
            raise self._invalid_event()
        state = self._turn_state(selected.get("status"))
        if state == "interrupted" and (thread.get("id"), turn_id) in self._cancelled:
            state = "cancelled"
        items = selected.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise self._invalid_event()
        return RuntimeObservation(
            state,
            turn_id,
            tuple(items),
            self._pending_for_thread(thread.get("id")),
        )

    def _pending_for_thread(self, thread_id: object) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(thread_id, str):
            return ()
        pending = []
        for request_id, approval in self._pending_approvals.items():
            if approval.thread_id == thread_id:
                pending.append(
                    {
                        "request_id": request_id,
                        "generation": approval.generation,
                        "request_kind": approval.method,
                    }
                )
        return tuple(pending)

    @staticmethod
    def _turn_state(status: object) -> str:
        states = {
            "inProgress": "active",
            "completed": "completed",
            "interrupted": "interrupted",
            "failed": "failed",
        }
        if status not in states:
            raise CodexAppServerAdapter._invalid_event()
        return states[status]

    @staticmethod
    def _thread_policy_params(session: RuntimeSession) -> dict[str, Any]:
        params: dict[str, Any] = {
            "cwd": session.cwd,
            "sandbox": THREAD_SANDBOX[session.sandbox],
            "approvalPolicy": "on-request",
            "approvalsReviewer": "user",
        }
        if session.sandbox == "workspace_write":
            params["config"] = {
                "sandbox_workspace_write": {
                    "writable_roots": [session.cwd],
                    "network_access": False,
                    "exclude_slash_tmp": True,
                    "exclude_tmpdir_env_var": True,
                }
            }
        return params

    @staticmethod
    def _turn_sandbox_policy(session: RuntimeSession) -> dict[str, Any]:
        if session.sandbox == "read_only":
            return {"type": "readOnly", "networkAccess": False}
        if session.sandbox == "workspace_write":
            return {
                "type": "workspaceWrite",
                "writableRoots": [session.cwd],
                "networkAccess": False,
                "excludeSlashTmp": True,
                "excludeTmpdirEnvVar": True,
            }
        return {"type": "dangerFullAccess"}

    @classmethod
    def _validate_effective_policy(cls, result: dict[str, Any], session: RuntimeSession) -> None:
        effective_sandbox = result.get("sandbox")
        valid_sandbox = effective_sandbox == cls._turn_sandbox_policy(session)
        valid_workspace_roots = True
        if session.sandbox == "workspace_write" and isinstance(effective_sandbox, dict):
            additional_roots = effective_sandbox.get("writableRoots")
            valid_sandbox = {
                **effective_sandbox,
                "writableRoots": [session.cwd],
            } == cls._turn_sandbox_policy(session) and additional_roots in ([], [session.cwd])
            if "runtimeWorkspaceRoots" in result:
                valid_workspace_roots = result["runtimeWorkspaceRoots"] == [session.cwd]
        if (
            result.get("cwd") != session.cwd
            or result.get("approvalPolicy") != "on-request"
            or result.get("approvalsReviewer") != "user"
            or not valid_sandbox
            or not valid_workspace_roots
        ):
            raise RuntimeErrorInfo("invalid_policy", "política efetiva divergente", False)
        if not isinstance(result.get("modelProvider"), str) or not result["modelProvider"]:
            raise cls._invalid_event()

    @staticmethod
    def _require_thread(session: RuntimeSession) -> str:
        if session.external_thread_id is None:
            raise RuntimeErrorInfo("thread_missing", "thread externo ausente", False)
        return session.external_thread_id

    @staticmethod
    def _invalid_event() -> RuntimeErrorInfo:
        return RuntimeErrorInfo("invalid_event", "evento inválido do Codex App Server", False)
