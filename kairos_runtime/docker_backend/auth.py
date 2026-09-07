"""Dedicated Codex-managed ChatGPT authentication; credentials never enter workers."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import stat
import time
from pathlib import Path

import httpx

from ..auth import RuntimeAuth
from ..errors import RuntimeErrorInfo

CHATGPT_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
_MAX_AUTH_BYTES = 64 * 1024


def _unavailable():
    return RuntimeErrorInfo("unavailable", "login ChatGPT dedicado indisponível", True)


def _claims(token):
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


async def _sse_bytes(response):
    media_type = response.headers.get("content-type", "")
    if media_type and not media_type.lower().startswith("text/event-stream"):
        raise _unavailable()
    validated = bool(media_type)
    prefix = bytearray()
    markers = (b"data:", b"event:", b"id:", b"retry:", b":")
    async for chunk in response.aiter_bytes():
        if validated:
            yield chunk
            continue
        # Some ChatGPT streaming responses omit Content-Type. Check a bounded
        # SSE preamble without buffering the complete event or forwarding HTML.
        prefix.extend(chunk)
        probe = bytes(prefix).lstrip(b"\xef\xbb\xbf \t\r\n")
        if len(prefix) - len(probe) > 4096:
            raise _unavailable()
        if probe.startswith(markers):
            validated = True
            yield bytes(prefix)
            prefix.clear()
        elif not any(marker.startswith(probe) for marker in markers):
            raise _unavailable()
    if not validated:
        raise _unavailable()


def read_chatgpt_headers(home: Path, *, minimum_validity: int = 0) -> dict[str, str]:
    """Read only broker-owned file credentials, with bounded and private diagnostics.

    JWT expiry is a freshness check, not signature verification: the trusted
    Codex login process owns this file and the upstream validates the bearer.
    """
    try:
        directory = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(directory)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError
            fd = os.open("auth.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        finally:
            os.close(directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_nlink != 1
                or info.st_size > _MAX_AUTH_BYTES
            ):
                raise ValueError
            raw = stream.read(_MAX_AUTH_BYTES + 1)
        if len(raw) > _MAX_AUTH_BYTES:
            raise ValueError
        data = json.loads(raw)
        if data.get("auth_mode") not in (None, "chatgpt") or data.get("OPENAI_API_KEY"):
            raise ValueError
        tokens = data["tokens"]
        access, account = tokens["access_token"], tokens["account_id"]
        if any(
            not isinstance(value, str)
            or not value
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
            for value in (access, account)
        ):
            raise ValueError
        expiry = _claims(access)["exp"]
        if (
            type(expiry) not in (int, float)
            or not math.isfinite(expiry)
            or not expiry > time.time() + minimum_validity
        ):
            raise ValueError
        id_token = tokens.get("id_token")
        if id_token and _claims(id_token).get("https://api.openai.com/auth", {}).get(
            "chatgpt_account_is_fedramp"
        ):
            raise ValueError
        return {
            "Authorization": "Bearer " + access,
            "ChatGPT-Account-Id": account,
            "version": "0.153.4",
            "originator": "codex_cli_rs",
            "User-Agent": "kairos-runtime/0.153.4",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
    except Exception:  # noqa: BLE001 - never expose credential data or parser diagnostics
        raise _unavailable() from None


class ChatGPTAuth(RuntimeAuth):
    def __init__(self, rpc, home: Path):
        super().__init__(rpc)
        self.home = home

    async def login(self, mode: str, api_key: str | None = None):
        if mode not in {"chatgpt", "chatgptDeviceCode"} or api_key is not None:
            raise RuntimeErrorInfo("invalid_event", "este backend exige login ChatGPT", False)
        return await super().login(mode)

    async def headers(self):
        async with self._operation_lock:
            rpc, generation = self._current_rpc()
            try:
                return read_chatgpt_headers(self.home, minimum_validity=300)
            except RuntimeErrorInfo:
                # Codex owns refresh. account/read alone does not attest success.
                await self._call(rpc, "account/read", {"refreshToken": True})
            if rpc is not self._rpc or generation != self._generation:
                raise _unavailable()
            for attempt in range(3):
                try:
                    return read_chatgpt_headers(self.home, minimum_validity=30)
                except RuntimeErrorInfo:
                    if attempt == 2:
                        raise
                    # Codex's file storage uses truncate/write rather than rename.
                    await asyncio.sleep(0.05)


class ChatGPTModelTransport:
    def __init__(self, auth: ChatGPTAuth, *, model: str, client=None):
        self.auth, self.model = auth, model
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(90, connect=15)
        )

    async def __call__(self, body):
        if body.get("model") != self.model:
            raise RuntimeErrorInfo("invalid_policy", "modelo não autorizado", False)
        request = dict(body, store=False, stream=True)
        include = request.get("include", [])
        if not isinstance(include, list) or any(not isinstance(item, str) for item in include):
            raise RuntimeErrorInfo("invalid_event", "requisição de modelo inválida", False)
        request["include"] = list(dict.fromkeys([*include, "reasoning.encrypted_content"]))
        headers = await self.auth.headers()
        try:
            async with self.client.stream(
                "POST", CHATGPT_RESPONSES_URL, headers=headers, json=request, follow_redirects=False
            ) as response:
                if response.status_code != 200:
                    raise _unavailable()
                async for chunk in _sse_bytes(response):
                    yield chunk
        except Exception:  # noqa: BLE001 - upstream error bodies may contain sensitive data
            raise _unavailable() from None

    async def aclose(self):
        if self._owns_client:
            await self.client.aclose()
