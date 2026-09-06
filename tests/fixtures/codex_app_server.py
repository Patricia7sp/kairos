from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from typing import Any

MODE = sys.argv[1] if len(sys.argv) > 1 else "adapter"

if MODE == "version":
    print("codex-cli 0.153.4")
    raise SystemExit(0)
if MODE == "bad-version":
    print("codex-cli 9.9.9")
    raise SystemExit(0)


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
        config = params.get("config")
        workspace = config.get("sandbox_workspace_write") if isinstance(config, dict) else None
        if isinstance(workspace, dict):
            policy = {
                "type": "workspaceWrite",
                "writableRoots": [
                    root for root in workspace.get("writable_roots", []) if root != params["cwd"]
                ],
                "networkAccess": workspace.get("network_access", False),
                "excludeSlashTmp": workspace.get("exclude_slash_tmp", False),
                "excludeTmpdirEnvVar": workspace.get("exclude_tmpdir_env_var", False),
            }
    return {
        "thread": thread(thread_id, params["cwd"]),
        "model": "gpt-5",
        "modelProvider": "openai",
        "cwd": params["cwd"],
        "runtimeWorkspaceRoots": [params["cwd"]],
        "approvalPolicy": params["approvalPolicy"],
        "approvalsReviewer": params["approvalsReviewer"],
        "sandbox": policy,
    }


pending_slow: int | str | None = None
pending_interrupt: int | str | None = None
interrupt_count = 0
reply_count = 0
last_thread_id = "thread-1"
last_cwd = tempfile.gettempdir()
host_turn: tuple[str, str] | None = None


def read_audit() -> dict[str, Any]:
    path = os.environ.get("KAIROS_FAKE_AUDIT")
    if not path or not os.path.exists(path):
        return {"thread_id": "thread-e2e", "thread_start": 0, "turn_start": 0}
    with open(path, encoding="utf-8") as source:
        return json.load(source)


def write_audit(audit: dict[str, Any]) -> None:
    path = os.environ["KAIROS_FAKE_AUDIT"]
    temporary = f"{path}.{os.getpid()}.tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(audit, target, sort_keys=True)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


if MODE == "hold-lock":
    with open(os.environ["KAIROS_FAKE_PID_FILE"], "w", encoding="utf-8") as pid_file:
        pid_file.write(str(os.getpid()))

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
                    "userAgent": "kairos/0.153.4 (Ubuntu 26.4.0; x86_64) dumb (kairos; 0.1.0)",
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
    elif method == "check/replies":
        send({"id": request_id, "result": {"replyCount": reply_count}})
    elif method == "thread/start":
        if MODE == "persistent-e2e":
            audit = read_audit()
            audit["thread_start"] += 1
            write_audit(audit)
            last_thread_id = audit["thread_id"]
            last_cwd = params["cwd"]
            send({"id": request_id, "result": effective_result(params, last_thread_id)})
            continue
        if params.get("sandbox") == "workspace-write" and params.get("config") != {
            "sandbox_workspace_write": {
                "writable_roots": [params["cwd"]],
                "network_access": False,
                "exclude_slash_tmp": True,
                "exclude_tmpdir_env_var": True,
            }
        }:
            send({"id": request_id, "error": {"code": -32602, "message": "bad config"}})
            continue
        last_thread_id = "thread-1"
        last_cwd = params["cwd"]
        send({"id": request_id, "result": effective_result(params, last_thread_id)})
    elif method == "thread/resume":
        if MODE in {"missing-thread", "invalid-resume"}:
            send(
                {
                    "id": request_id,
                    "error": {
                        "code": -32600,
                        "message": "no rollout found for thread id sensitive-id"
                        if MODE == "missing-thread"
                        else "invalid request sensitive-details",
                    },
                }
            )
            continue
        if MODE in {"attach", "attach-barrier"}:
            send(
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": params["threadId"],
                        "turnId": "external-turn-1",
                        "itemId": "i1",
                        "delta": "during resume",
                    },
                }
            )
        if MODE == "attach":
            send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": params["threadId"],
                        "turn": {"id": "external-turn-1", "status": "completed"},
                    },
                }
            )
        if params.get("sandbox") == "workspace-write" and params.get("config") != {
            "sandbox_workspace_write": {
                "writable_roots": [params["cwd"]],
                "network_access": False,
                "exclude_slash_tmp": True,
                "exclude_tmpdir_env_var": True,
            }
        }:
            send({"id": request_id, "error": {"code": -32602, "message": "bad config"}})
            continue
        last_thread_id = params["threadId"]
        last_cwd = params["cwd"]
        send({"id": request_id, "result": effective_result(params, last_thread_id)})
    elif method == "turn/start":
        if MODE == "persistent-e2e":
            audit = read_audit()
            audit["turn_start"] += 1
            write_audit(audit)
            turn_id = f"external-turn-{audit['turn_start']}"
            host_turn = (params["threadId"], turn_id)
            approval_rpc_id = 700 + audit["turn_start"]
            send(
                {
                    "id": request_id,
                    "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}},
                }
            )
            send(
                {
                    "id": approval_rpc_id,
                    "method": "item/fileChange/requestApproval",
                    "params": {
                        "itemId": f"change-{audit['turn_start']}",
                        "startedAtMs": 11,
                        "threadId": params["threadId"],
                        "turnId": turn_id,
                        "grantRoot": params["cwd"],
                    },
                }
            )
            continue
        turn_id = "external-turn-1"
        if MODE == "host-approval":
            host_turn = (params["threadId"], turn_id)
            send(
                {
                    "id": request_id,
                    "result": {"turn": {"id": turn_id, "items": [], "status": "inProgress"}},
                }
            )
            send(
                {
                    "id": 700,
                    "method": "item/fileChange/requestApproval",
                    "params": {
                        "itemId": "change-1",
                        "startedAtMs": 11,
                        "threadId": params["threadId"],
                        "turnId": turn_id,
                        "grantRoot": "/outside",
                    },
                }
            )
            continue
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
                    if MODE in {"unknown-request", "unknown-request-other-turn"}
                    else "item/fileChange/requestApproval"
                ),
                "params": {
                    "itemId": "change-1",
                    "startedAtMs": 11,
                    "threadId": params["threadId"],
                    "turnId": ("other-turn" if MODE == "unknown-request-other-turn" else turn_id),
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
        if MODE == "attach-barrier":
            snapshot = thread(
                params["threadId"],
                last_cwd,
                turns=[
                    {
                        "id": "external-turn-1",
                        "status": "inProgress",
                        "items": [{"id": "i1", "type": "agentMessage", "text": "during resume"}],
                    }
                ],
            )
            snapshot["status"] = {"type": "active", "activeFlags": []}
            batch = [
                {"id": request_id, "result": {"thread": snapshot}},
                {
                    "method": "item/agentMessage/delta",
                    "params": {
                        "threadId": params["threadId"],
                        "turnId": "external-turn-1",
                        "itemId": "i1",
                        "delta": " after response",
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": params["threadId"],
                        "turn": {"id": "external-turn-1", "status": "completed"},
                    },
                },
            ]
            sys.stdout.write("".join(json.dumps(item) + "\n" for item in batch))
            sys.stdout.flush()
            continue
        turn_status = "interrupted" if params["threadId"] == "interrupted-thread" else "completed"
        turn_id = "cancelled-turn" if turn_status == "interrupted" else "external-turn-1"
        send(
            {
                "id": request_id,
                "result": {
                    "thread": thread(
                        params["threadId"],
                        last_cwd,
                        turns=[
                            {
                                "id": turn_id,
                                "items": [
                                    {
                                        "id": "i1",
                                        "type": "agentMessage",
                                        "text": "completed while attaching",
                                    }
                                ]
                                if MODE == "attach-terminal-only"
                                else [],
                                "status": turn_status,
                            }
                        ],
                    )
                },
            }
        )
    elif method in {"turn/interrupt", "thread/unsubscribe"}:
        if method == "turn/interrupt" and MODE == "late-interrupt":
            pending_interrupt = request_id
            interrupt_count += 1
            send({"method": "test/interruptPending", "params": {"threadId": params["threadId"]}})
            continue
        result = {} if method == "turn/interrupt" else {"status": "unsubscribed"}
        send({"id": request_id, "result": result})
    elif method == "release-interrupt":
        send({"id": pending_interrupt, "result": {}})
        pending_interrupt = None
        send({"id": request_id, "result": {"interruptCount": interrupt_count}})
    elif request_id is not None and method is None:
        # Client reply to a server request.
        reply_count += 1
        if MODE in {"host-approval", "persistent-e2e"} and host_turn is not None:
            thread_id, turn_id = host_turn
            send(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread_id,
                        "turn": {"id": turn_id, "items": [], "status": "completed"},
                    },
                }
            )
        continue
    else:
        send({"id": request_id, "result": {"ok": True}})

if MODE == "hold-lock":
    while True:
        time.sleep(60)
