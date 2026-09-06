"""Transient orchestration of Codex-managed account authentication."""

from __future__ import annotations

import asyncio
from typing import Any

from .codex_rpc import CodexRpc
from .errors import RuntimeErrorInfo

__all__ = ["RuntimeAuth"]


_LOGIN_MODES = frozenset({"apiKey", "chatgpt", "chatgptDeviceCode"})
_PLAN_TYPES = frozenset(
    {
        "free",
        "go",
        "plus",
        "pro",
        "prolite",
        "team",
        "self_serve_business_prolite",
        "self_serve_business_usage_based",
        "business",
        "ent26",
        "enterprise_cbp_automation",
        "enterprise_cbp_usage_based",
        "enterprise",
        "edu",
        "edu_plus",
        "edu_pro",
        "unknown",
    }
)


def _invalid() -> RuntimeErrorInfo:
    return RuntimeErrorInfo("invalid_event", "requisição de autenticação inválida", False)


class RuntimeAuth:
    """Owns no credentials; Codex persists and refreshes its managed account."""

    def __init__(self, rpc: CodexRpc) -> None:
        self._rpc = rpc
        self._generation = rpc.generation
        self._subscription = rpc.subscribe_account()
        self._listener = asyncio.create_task(
            self._listen(self._subscription), name="runtime-account-notifications"
        )
        self._operation_lock = asyncio.Lock()
        self._state_lock = asyncio.Lock()
        self._pending_login_id: str | None = None
        self._early_completion: tuple[str, bool] | None = None
        self._login_state = "idle"
        self._inflight_call: asyncio.Task[Any] | None = None
        self._closed = False

    async def status(self) -> dict[str, Any]:
        async with self._operation_lock:
            rpc, generation = self._current_rpc()
            result = await self._call(rpc, "account/read", {"refreshToken": False})
            if rpc is not self._rpc or generation != self._generation:
                raise RuntimeErrorInfo("unavailable", "runtime indisponível", True)
            account = _parse_account(result)
            async with self._state_lock:
                login_state = self._login_state
            return {
                "requires_openai_auth": result["requiresOpenaiAuth"],
                "authenticated": account is not None,
                "auth_mode": account["type"] if account is not None else None,
                "email": account.get("email") if account is not None else None,
                "plan_type": account.get("planType") if account is not None else None,
                "login_state": login_state,
            }

    async def login(self, mode: str, api_key: str | None = None) -> dict[str, Any]:
        if mode not in _LOGIN_MODES:
            raise _invalid()
        if mode == "apiKey":
            if not isinstance(api_key, str) or not api_key:
                raise _invalid()
            params = {"type": "apiKey", "apiKey": api_key}
        else:
            if api_key is not None:
                raise _invalid()
            params = {"type": mode}
        async with self._operation_lock:
            rpc, generation = self._current_rpc()
            async with self._state_lock:
                if self._pending_login_id is not None:
                    raise RuntimeErrorInfo("session_busy", "login já está pendente", False)
                self._early_completion = None
                self._login_state = "starting"
            try:
                result = await self._call(rpc, "account/login/start", params)
                response = _parse_login_response(mode, result)
            except BaseException:
                async with self._state_lock:
                    if generation == self._generation:
                        self._pending_login_id = None
                        self._early_completion = None
                        self._login_state = "failed"
                raise
            if rpc is not self._rpc or generation != self._generation:
                raise RuntimeErrorInfo("unavailable", "runtime indisponível", True)
            async with self._state_lock:
                self._pending_login_id = response.get("login_id")
                self._login_state = response["state"]
                if (
                    self._pending_login_id is not None
                    and self._early_completion is not None
                    and self._early_completion[0] == self._pending_login_id
                ):
                    self._pending_login_id = None
                    self._login_state = "succeeded" if self._early_completion[1] else "failed"
                self._early_completion = None
            return response

    async def cancel(self, login_id: str) -> None:
        if not isinstance(login_id, str) or not login_id:
            raise _invalid()
        async with self._operation_lock:
            rpc, generation = self._current_rpc()
            async with self._state_lock:
                if login_id != self._pending_login_id:
                    raise _invalid()
            result = await self._call(rpc, "account/login/cancel", {"loginId": login_id})
            if result.get("status") not in {"canceled", "notFound"}:
                raise _invalid()
            if rpc is not self._rpc or generation != self._generation:
                raise RuntimeErrorInfo("unavailable", "runtime indisponível", True)
            async with self._state_lock:
                self._pending_login_id = None
                self._early_completion = None
                self._login_state = "cancelled"

    async def logout(self) -> None:
        async with self._operation_lock:
            rpc, generation = self._current_rpc()
            result = await self._call(rpc, "account/logout", {})
            if not isinstance(result, dict):
                raise _invalid()
            if rpc is not self._rpc or generation != self._generation:
                raise RuntimeErrorInfo("unavailable", "runtime indisponível", True)
            async with self._state_lock:
                self._pending_login_id = None
                self._early_completion = None
                self._login_state = "idle"

    async def replace_rpc(self, rpc: CodexRpc) -> None:
        """Bind a new supervisor generation and invalidate old login state."""
        async with self._operation_lock:
            old_rpc, old_subscription, old_listener = (
                self._rpc,
                self._subscription,
                self._listener,
            )
            old_rpc.unsubscribe(old_subscription)
            old_listener.cancel()
            await asyncio.gather(old_listener, return_exceptions=True)
            self._rpc = rpc
            self._generation = rpc.generation
            self._subscription = rpc.subscribe_account()
            self._listener = asyncio.create_task(
                self._listen(self._subscription), name="runtime-account-notifications"
            )
            async with self._state_lock:
                self._pending_login_id = None
                self._early_completion = None
                self._login_state = "idle"

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        inflight = self._inflight_call
        if inflight is not None:
            inflight.cancel()
            await asyncio.gather(inflight, return_exceptions=True)
        async with self._operation_lock:
            self._rpc.unsubscribe(self._subscription)
            self._listener.cancel()
            await asyncio.gather(self._listener, return_exceptions=True)
            async with self._state_lock:
                self._pending_login_id = None
                self._early_completion = None
                self._login_state = "idle"

    async def _call(self, rpc: CodexRpc, method: str, params: dict[str, Any]) -> Any:
        call = asyncio.create_task(rpc.call(method, params))
        self._inflight_call = call
        try:
            return await call
        finally:
            if self._inflight_call is call:
                self._inflight_call = None

    def _current_rpc(self) -> tuple[CodexRpc, str]:
        if self._closed:
            raise RuntimeErrorInfo("unavailable", "autenticação do runtime encerrada", False)
        return self._rpc, self._generation

    async def _listen(self, subscription) -> None:
        try:
            while True:
                message = await subscription.get()
                if subscription.generation != self._generation:
                    return
                method = message.get("method")
                params = message.get("params")
                if method == "account/login/completed":
                    await self._login_completed(subscription.generation, params)
                elif method == "account/updated":
                    _validate_account_updated(params)
        except (RuntimeErrorInfo, asyncio.CancelledError):
            return

    async def _login_completed(self, generation: str, params: object) -> None:
        if not isinstance(params, dict) or type(params.get("success")) is not bool:
            return
        login_id = params.get("loginId")
        if not isinstance(login_id, str) or not login_id:
            return
        async with self._state_lock:
            if generation != self._generation:
                return
            if self._pending_login_id is None and self._login_state == "starting":
                self._early_completion = (login_id, params["success"])
                return
            if login_id != self._pending_login_id:
                return
            self._pending_login_id = None
            self._early_completion = None
            self._login_state = "succeeded" if params["success"] else "failed"


def _parse_account(result: object) -> dict[str, Any] | None:
    if not isinstance(result, dict) or type(result.get("requiresOpenaiAuth")) is not bool:
        raise _invalid()
    account = result.get("account")
    if account is None:
        return None
    if not isinstance(account, dict):
        raise _invalid()
    mode = account.get("type")
    if mode == "apiKey":
        return {"type": "apiKey"}
    if mode != "chatgpt":
        raise _invalid()
    email = account.get("email")
    plan = account.get("planType")
    if (email is not None and not isinstance(email, str)) or plan not in _PLAN_TYPES:
        raise _invalid()
    return {"type": "chatgpt", "email": email, "planType": plan}


def _parse_login_response(mode: str, result: object) -> dict[str, Any]:
    if not isinstance(result, dict) or result.get("type") != mode:
        raise _invalid()
    if mode == "apiKey":
        return {"mode": "apiKey", "state": "succeeded"}
    login_id = result.get("loginId")
    if not isinstance(login_id, str) or not login_id:
        raise _invalid()
    if mode == "chatgpt":
        auth_url = result.get("authUrl")
        if not isinstance(auth_url, str) or not auth_url:
            raise _invalid()
        return {
            "mode": mode,
            "state": "pending",
            "login_id": login_id,
            "auth_url": auth_url,
        }
    user_code = result.get("userCode")
    verification_url = result.get("verificationUrl")
    if not isinstance(user_code, str) or not user_code:
        raise _invalid()
    if not isinstance(verification_url, str) or not verification_url:
        raise _invalid()
    return {
        "mode": mode,
        "state": "pending",
        "login_id": login_id,
        "user_code": user_code,
        "verification_url": verification_url,
    }


def _validate_account_updated(params: object) -> None:
    if not isinstance(params, dict):
        return
    mode = params.get("authMode")
    plan = params.get("planType")
    if mode not in {None, "apikey", "chatgpt"} or plan not in {None, *_PLAN_TYPES}:
        return
