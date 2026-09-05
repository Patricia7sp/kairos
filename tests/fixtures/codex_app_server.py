from __future__ import annotations

import json
import os
import sys
import tempfile
from typing import Any

MODE = sys.argv[1] if len(sys.argv) > 1 else "adapter"


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def sandbox_policy(name: str) -> dict[str, Any]:
    if name == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    if name == "workspace-write":
        return {
            "type": "workspaceWrite",
            "writableRoots": [],
            "networkAccess": False,
            "excludeSlashTmp": True,
            "excludeTmpdirEnvVar": True,
        }
    return {"type": "dangerFullAccess"}


def thread(
    thread_id: str, cwd: str, *, turns: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "id": thread_id,
        "preview": "",
        "ephemeral": False,
        "modelProvider": "openai",
        "createdAt": 1,
        "updatedAt": 1,
        "status": {"type": "idle"},
        "path": None,
        "cwd": cwd,
        "cliVersion": "0.153.4",
        "source": "appServer",
        "agentNickname": None,
        "agentRole": None,
        "gitInfo": None,
        "name": None,
        "turns": turns or [],
        "sessionId": "fake-session",
        "forkedFromId": None,
        "parentThreadId": None,
        "projectId": None,
    }


def effective_result(params: dict[str, Any], thread_id: str) -> dict[str, Any]:
    sandbox = params.get("sandbox", "workspace-write")
    policy = sandbox_policy(sandbox)
    if policy["type"] == "workspaceWrite":
        policy["writableRoots"] = [params["cwd"]]
    return {
        "thread": thread(thread_id, params["cwd"]),
        "model": "gpt-5",
        "modelProvider": "openai",
        "cwd": params["cwd"],
        "approvalPolicy": params["approvalPolicy"],
        "approvalsReviewer": params["approvalsReviewer"],
        "sandbox": policy,
    }


pending_slow: int | str | None = None
last_thread_id = "thread-1"
last_cwd = tempfile.gettempdir()

for raw_line in sys.stdin:
    message = json.loads(raw_line)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})

    if method == "initialize":
        if MODE == "bad-initialize":
            send({"id": request_id, "result": {"userAgent": "codex/0.153.4"}})
            continue
        send(
            {
                "id": request_id,
                "result": {
                    "codexHome": os.environ["CODEX_HOME"],
                    "platformFamily": "unix",
                    "platformOs": "linux",
                    "userAgent": "codex/0.153.4",
                },
            }
        )
        continue
    if method == "initialized":
        continue

    if MODE == "invalid-json":
        sys.stdout.write("{broken json\n")
        sys.stdout.flush()
        continue
    if MODE == "oversized":
        sys.stdout.write("x" * (1024 * 1024 + 1) + "\n")
        sys.stdout.flush()
        continue
    if MODE == "eof":
        raise SystemExit(0)
    if MODE == "stderr":
        os.write(2, b"secret-auth-token\n")

    if method == "slow":
        pending_slow = request_id
    elif method == "fast":
        send({"id": request_id, "result": {"value": "fast"}})
        send({"method": "test/note", "params": {"threadId": "thread-1", "value": 3}})
        if pending_slow is not None:
            send({"id": pending_slow, "result": {"value": "slow"}})
            pending_slow = None
    elif method == "approval-test":
        send(
            {
                "id": "rpc-request-9",
                "method": "item/commandExecution/requestApproval",
                "params": {
                    "itemId": "item-9",
                    "startedAtMs": 10,
                    "threadId": "thread-1",
                    "turnId": "turn-9",
                    "approvalId": "approval-callback-9",
                    "command": "touch outside",
                },
            }
        )
        send({"id": request_id, "result": {"ok": True}})
    elif method == "unscoped-request-test":
        send({"id": "unscoped-1", "method": "future/globalRequest", "params": {}})
        reply = json.loads(next(sys.stdin))
        if reply.get("id") == "unscoped-1" and reply.get("error", {}).get("code") == -32601:
            send({"id": request_id, "result": {"rejected": True}})
        else:
            send({"id": request_id, "error": {"code": -32000, "message": "unsafe reply"}})
    elif method == "thread/start":
        last_thread_id = "thread-1"
        last_cwd = params["cwd"]
        send({"id": request_id, "result": effective_result(params, last_thread_id)})
    elif method == "thread/resume":
        last_thread_id = params["threadId"]
        last_cwd = params["cwd"]
        send({"id": request_id, "result": effective_result(params, last_thread_id)})
    elif method == "turn/start":
        turn_id = "external-turn-1"
        sandbox = params.get("sandboxPolicy", {})
        valid_sandbox = sandbox in (
            {"type": "readOnly", "networkAccess": False},
            {"type": "dangerFullAccess"},
            {
                "type": "workspaceWrite",
                "writableRoots": [params.get("cwd")],
                "networkAccess": False,
                "excludeSlashTmp": True,
                "excludeTmpdirEnvVar": True,
            },
        )
        valid_turn = (
            params.get("approvalPolicy") == "on-request"
            and params.get("approvalsReviewer") == "user"
            and isinstance(params.get("cwd"), str)
            and params.get("clientUserMessageId") == "local-turn-1"
            and params.get("input") == [{"type": "text", "text": "hello"}]
            and "model" not in params
            and "modelProvider" not in params
            and valid_sandbox
        )
        if not valid_turn:
            send({"id": request_id, "error": {"code": -32602, "message": "bad turn params"}})
            continue
        send(
            {
                "method": "item/agentMessage/delta",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "itemId": "message-1",
                    "delta": "early ",
                },
            }
        )
        send(
            {
                "id": 700,
                "method": (
                    "future/requestApproval"
                    if MODE == "unknown-request"
                    else "item/fileChange/requestApproval"
                ),
                "params": {
                    "itemId": "change-1",
                    "startedAtMs": 11,
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "grantRoot": "/outside",
                },
            }
        )
        send(
            {
                "method": "error",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "willRetry": True,
                    "error": {"message": "secret upstream error", "additionalDetails": "token"},
                },
            }
        )
        send(
            {
                "method": "item/started",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "startedAtMs": 12,
                    "item": {
                        "id": "command-1",
                        "type": "commandExecution",
                        "command": "pwd",
                        "commandActions": [],
                        "cwd": last_cwd,
                        "status": "inProgress",
                    },
                },
            }
        )
        send(
            {
                "id": request_id,
                "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}},
            }
        )
        send(
            {
                "method": "item/completed",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "completedAtMs": 20,
                    "item": {"id": "message-1", "type": "agentMessage", "text": "early final"},
                },
            }
        )
        send(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "threadId": params["threadId"],
                    "turnId": turn_id,
                    "tokenUsage": {
                        "total": {
                            "inputTokens": 2,
                            "cachedInputTokens": 0,
                            "outputTokens": 3,
                            "reasoningOutputTokens": 1,
                            "totalTokens": 5,
                        },
                        "last": {
                            "inputTokens": 2,
                            "cachedInputTokens": 0,
                            "outputTokens": 3,
                            "reasoningOutputTokens": 1,
                            "totalTokens": 5,
                        },
                        "modelContextWindow": 100,
                    },
                },
            }
        )
        send(
            {
                "method": "turn/completed",
                "params": {
                    "threadId": params["threadId"],
                    "turn": {"id": turn_id, "items": [], "status": "completed"},
                },
            }
        )
    elif method == "thread/read":
        turn_status = "interrupted" if params["threadId"] == "interrupted-thread" else "completed"
        turn_id = "cancelled-turn" if turn_status == "interrupted" else "external-turn-1"
        send(
            {
                "id": request_id,
                "result": {
                    "thread": thread(
                        params["threadId"],
                        last_cwd,
                        turns=[{"id": turn_id, "items": [], "status": turn_status}],
                    )
                },
            }
        )
    elif method in {"turn/interrupt", "thread/unsubscribe"}:
        result = {} if method == "turn/interrupt" else {"status": "unsubscribed"}
        send({"id": request_id, "result": result})
    elif request_id is not None and method is None:
        # Client reply to a server request.
        continue
    else:
        send({"id": request_id, "result": {"ok": True}})
