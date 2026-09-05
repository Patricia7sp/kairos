from __future__ import annotations

import asyncio
import functools
from types import SimpleNamespace

import pytest

from kairos_runtime.auth import RuntimeAuth
from kairos_runtime.codex_rpc import CodexRpc
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.host import _Host


def async_test(function):
    @functools.wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return run


class AccountSubscription:
    def __init__(self, rpc):
        self.rpc = rpc
        self.generation = rpc.generation
        self.queue = asyncio.Queue()

    async def get(self):
        return await self.queue.get()


class FakeRpc:
    def __init__(self, generation="generation-1"):
        self.generation = generation
        self.calls = []
        self.subscriptions = set()
        self.account = None
        self.requires_auth = True
        self.login_responses = {
            "chatgpt": {
                "type": "chatgpt",
                "loginId": "browser-login",
                "authUrl": "https://example.test/browser",
            },
            "chatgptDeviceCode": {
                "type": "chatgptDeviceCode",
                "loginId": "device-login",
                "userCode": "ABCD-EFGH",
                "verificationUrl": "https://example.test/device",
            },
            "apiKey": {"type": "apiKey"},
        }

    def subscribe_account(self):
        subscription = AccountSubscription(self)
        self.subscriptions.add(subscription)
        return subscription

    def unsubscribe(self, subscription):
        self.subscriptions.discard(subscription)

    async def call(self, method, params):
        self.calls.append((method, params))
        if method == "account/read":
            return {"requiresOpenaiAuth": self.requires_auth, "account": self.account}
        if method == "account/login/start":
            response = self.login_responses[params["type"]]
            if params["type"] == "apiKey":
                self.account = {"type": "apiKey"}
            return response
        if method == "account/login/cancel":
            return {"status": "canceled"}
        if method == "account/logout":
            self.account = None
            return {}
        raise AssertionError(method)

    def emit(self, method, params):
        message = {"method": method, "params": params}
        for subscription in tuple(self.subscriptions):
            subscription.queue.put_nowait(message)


class MemoryWriter:
    def __init__(self):
        self.frames = []

    def write(self, frame):
        self.frames.append(frame)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


@async_test
async def test_rpc_routes_only_account_notifications_to_account_subscribers():
    reader = asyncio.StreamReader()
    writer = MemoryWriter()
    rpc = CodexRpc(reader, writer, generation="generation-1")
    account = rpc.subscribe_account()
    thread = rpc.subscribe("thread-1")
    await rpc.start()
    try:
        reader.feed_data(
            b'{"method":"account/updated","params":{"authMode":"apikey","planType":null}}\n'
            b'{"method":"future/global","params":{"secret":"not-routed"}}\n'
        )
        assert await asyncio.wait_for(account.get(), 1) == {
            "method": "account/updated",
            "params": {"authMode": "apikey", "planType": None},
        }
        await asyncio.sleep(0)
        assert thread.queue.empty()
        assert account.queue.empty()
    finally:
        rpc.unsubscribe(account)
        rpc.unsubscribe(thread)
        await rpc.aclose()


@async_test
async def test_status_without_login_is_a_fixed_allowlisted_shape():
    rpc = FakeRpc()
    auth = RuntimeAuth(rpc)
    try:
        assert await auth.status() == {
            "requires_openai_auth": True,
            "authenticated": False,
            "auth_mode": None,
            "email": None,
            "plan_type": None,
            "login_state": "idle",
        }
        assert rpc.calls == [("account/read", {"refreshToken": False})]
    finally:
        await auth.aclose()


@pytest.mark.parametrize(
    ("mode", "api_key", "expected_params", "expected"),
    [
        (
            "chatgpt",
            None,
            {"type": "chatgpt"},
            {
                "mode": "chatgpt",
                "state": "pending",
                "login_id": "browser-login",
                "auth_url": "https://example.test/browser",
            },
        ),
        (
            "chatgptDeviceCode",
            None,
            {"type": "chatgptDeviceCode"},
            {
                "mode": "chatgptDeviceCode",
                "state": "pending",
                "login_id": "device-login",
                "user_code": "ABCD-EFGH",
                "verification_url": "https://example.test/device",
            },
        ),
        (
            "apiKey",
            "sk-runtime-sentinel",
            {"type": "apiKey", "apiKey": "sk-runtime-sentinel"},
            {"mode": "apiKey", "state": "succeeded"},
        ),
    ],
)
@async_test
async def test_login_accepts_only_the_three_v1_modes_and_returns_allowlisted_fields(
    mode, api_key, expected_params, expected
):
    rpc = FakeRpc()
    auth = RuntimeAuth(rpc)
    try:
        assert await auth.login(mode, api_key) == expected
        assert rpc.calls == [("account/login/start", expected_params)]
        assert "sk-runtime-sentinel" not in repr(vars(auth))
    finally:
        await auth.aclose()


@async_test
async def test_login_completion_is_generation_bound_and_clears_pending_login():
    old_rpc = FakeRpc("generation-old")
    auth = RuntimeAuth(old_rpc)
    new_rpc = FakeRpc("generation-new")
    try:
        await auth.login("chatgpt")
        await auth.replace_rpc(new_rpc)
        old_rpc.emit(
            "account/login/completed",
            {"loginId": "browser-login", "success": True},
        )
        new_rpc.emit(
            "account/login/completed",
            {"loginId": "browser-login", "success": True},
        )
        await asyncio.sleep(0)
        assert (await auth.status())["login_state"] == "idle"

        await auth.login("chatgpt")
        new_rpc.emit(
            "account/login/completed",
            {"loginId": None, "success": True},
        )
        await asyncio.sleep(0)
        assert (await auth.status())["login_state"] == "pending"
        new_rpc.emit(
            "account/login/completed",
            {"loginId": "browser-login", "success": False, "error": "SECRET"},
        )
        await asyncio.sleep(0)
        assert (await auth.status())["login_state"] == "failed"
        assert "SECRET" not in repr(vars(auth))
    finally:
        await auth.aclose()


@async_test
async def test_completion_arriving_before_login_response_is_correlated():
    class EarlyCompletionRpc(FakeRpc):
        async def call(self, method, params):
            if method == "account/login/start":
                self.calls.append((method, params))
                self.emit(
                    "account/login/completed",
                    {"loginId": "browser-login", "success": True},
                )
                await asyncio.sleep(0)
                return self.login_responses["chatgpt"]
            return await super().call(method, params)

    rpc = EarlyCompletionRpc()
    auth = RuntimeAuth(rpc)
    try:
        response = await auth.login("chatgpt")
        assert response["state"] == "pending"
        assert (await auth.status())["login_state"] == "succeeded"
    finally:
        await auth.aclose()


@async_test
async def test_cancel_uses_schema_and_clears_transient_login_state():
    rpc = FakeRpc()
    auth = RuntimeAuth(rpc)
    try:
        await auth.login("chatgptDeviceCode")
        await auth.cancel("device-login")
        assert rpc.calls[-1] == ("account/login/cancel", {"loginId": "device-login"})
        assert (await auth.status())["login_state"] == "cancelled"
        with pytest.raises(RuntimeErrorInfo) as raised:
            await auth.cancel("device-login")
        assert raised.value.code == "invalid_event"
    finally:
        await auth.aclose()


@pytest.mark.parametrize("mode", ["chatgptAuthTokens", "amazonBedrock", "headers"])
@async_test
async def test_login_rejects_non_v1_and_external_token_modes(mode):
    rpc = FakeRpc()
    auth = RuntimeAuth(rpc)
    try:
        with pytest.raises(RuntimeErrorInfo) as raised:
            await auth.login(mode)
        assert raised.value.code == "invalid_event"
        assert rpc.calls == []
    finally:
        await auth.aclose()


@async_test
async def test_host_refuses_logout_for_durable_work_under_the_mutation_fence():
    checked = asyncio.Event()
    allow_logout = asyncio.Event()
    calls = []

    class Service:
        async def ensure_account_idle(self):
            calls.append("check")
            checked.set()
            await allow_logout.wait()

        async def submit(self, **params):
            calls.append(("submit", params))
            return "turn-1"

    class Auth:
        async def logout(self):
            calls.append("logout")

    host = _Host(
        config=SimpleNamespace(enabled=True),
        service=Service(),
        auth=Auth(),
    )
    logout = asyncio.create_task(host._dispatch("account.logout", {}))
    await checked.wait()
    submit = asyncio.create_task(
        host._dispatch(
            "turn.submit",
            {"session_id": "s1", "content": "hello", "idempotency_key": "one"},
        )
    )
    await asyncio.sleep(0)
    assert calls == ["check"]
    allow_logout.set()
    await logout
    assert await submit == "turn-1"
    assert calls == [
        "check",
        "logout",
        (
            "submit",
            {"session_id": "s1", "content": "hello", "idempotency_key": "one"},
        ),
    ]


@async_test
async def test_service_logout_guard_counts_queued_and_uncertain_durable_work():
    class Store:
        async def nonterminal_turns(self):
            return ({"id": "queued", "state": "queued", "send_state": "not_sent"},)

    from kairos_runtime.service import AgentRuntimeService

    service = AgentRuntimeService(Store(), SimpleNamespace(), allowed_directories=())
    with pytest.raises(RuntimeErrorInfo) as raised:
        await service.ensure_account_idle()
    assert raised.value.code == "session_busy"
