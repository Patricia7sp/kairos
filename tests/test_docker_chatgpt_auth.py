import asyncio
import base64
import json
import time

import httpx
import pytest

from kairos_runtime.docker_backend.auth import (
    ChatGPTAuth,
    ChatGPTModelTransport,
    read_chatgpt_headers,
)
from kairos_runtime.errors import RuntimeErrorInfo


def token(exp):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


def credentials(home, *, exp=None):
    access = token(exp or time.time() + 3600)
    path = home / "auth.json"
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {
                    "access_token": access,
                    "refresh_token": "never-forward",
                    "account_id": "account-test",
                },
            }
        )
    )
    path.chmod(0o600)
    return access


def test_only_access_token_and_account_are_forwarded_from_dedicated_home(tmp_path):
    access = credentials(tmp_path)
    headers = read_chatgpt_headers(tmp_path)
    assert headers["Authorization"] == "Bearer " + access
    assert headers["ChatGPT-Account-Id"] == "account-test"
    assert "never-forward" not in json.dumps(headers)


@pytest.mark.parametrize(
    "bad", ["symlink", "permissions", "expired", "api-key", "bad-json", "infinite"]
)
def test_unsafe_or_unusable_auth_is_refused_without_secret_diagnostics(tmp_path, bad):
    credentials(tmp_path)
    path = tmp_path / "auth.json"
    if bad == "symlink":
        saved = tmp_path / "other"
        path.rename(saved)
        path.symlink_to(saved)
    elif bad == "permissions":
        path.chmod(0o644)
    elif bad == "expired":
        credentials(tmp_path, exp=time.time() - 10)
    elif bad == "api-key":
        path.write_text('{"OPENAI_API_KEY":"never-forward"}')
    elif bad == "infinite":
        credentials(tmp_path, exp=float("inf"))
    else:
        path.write_text("never-forward")
    with pytest.raises(Exception) as caught:
        read_chatgpt_headers(tmp_path)
    assert "never-forward" not in str(caught.value)


def test_transport_pins_endpoint_does_not_redirect_and_hides_upstream_errors():
    async def scenario():
        calls = []

        class Auth:
            async def headers(self):
                return {"Authorization": "Bearer sensitive", "ChatGPT-Account-Id": "account-test"}

        async def upstream(request):
            calls.append(request)
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.invalid"},
                content=b"sensitive upstream error",
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(upstream), follow_redirects=False)
        transport = ChatGPTModelTransport(Auth(), model="gpt-5.5", client=client)
        try:
            with pytest.raises(Exception) as caught:
                _ = [chunk async for chunk in transport({"model": "gpt-5.5", "input": []})]
            assert "sensitive" not in str(caught.value)
            assert len(calls) == 1
            assert str(calls[0].url) == "https://chatgpt.com/backend-api/codex/responses"
            body = json.loads(calls[0].content)
            assert body["store"] is False and body["stream"] is True
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_chatgpt_auth_refreshes_near_expiry_and_rechecks_the_file(tmp_path):
    async def scenario():
        from types import SimpleNamespace

        queue = asyncio.Queue()
        calls = []
        refresh_succeeds = False

        async def call(method, params):
            calls.append((method, params))
            if refresh_succeeds:
                credentials(tmp_path)
            return {"requiresOpenaiAuth": True, "account": {"type": "chatgpt"}}

        rpc = SimpleNamespace(
            generation="auth-generation",
            call=call,
            subscribe_account=lambda: SimpleNamespace(generation="auth-generation", get=queue.get),
            unsubscribe=lambda _: None,
        )
        auth = ChatGPTAuth(rpc, tmp_path)
        try:
            credentials(tmp_path)
            await auth.headers()
            assert calls == []
            credentials(tmp_path, exp=time.time() - 1)
            with pytest.raises(RuntimeErrorInfo):
                await auth.headers()
            assert calls == [("account/read", {"refreshToken": True})]
            refresh_succeeds = True
            await auth.headers()
            assert len(calls) == 2
            with pytest.raises(RuntimeErrorInfo):
                await auth.login("apiKey", "never-forward")
            assert len(calls) == 2
        finally:
            await auth.aclose()

    asyncio.run(scenario())


def test_fedramp_account_is_refused_until_supported(tmp_path):
    credentials(tmp_path)
    path = tmp_path / "auth.json"
    data = json.loads(path.read_text())
    claims = {"https://api.openai.com/auth": {"chatgpt_account_is_fedramp": True}}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    data["tokens"]["id_token"] = f"header.{payload}.signature"
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeErrorInfo):
        read_chatgpt_headers(tmp_path)
