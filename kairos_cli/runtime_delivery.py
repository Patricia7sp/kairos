"""Explicit operator export, review, apply and separate draft-PR publication.

Receipts contain public change/test evidence, bound to the commit message. They
are integrity records, not signatures; trusted operators authorize test argv and
own the Git repository. No runtime worker credentials are read here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import stat
import tempfile
from pathlib import Path

from kairos_cli import runtime_delivery_git as ops
from kairos_runtime.experimental.snapshot import snapshot_project
from kairos_runtime.projects import _directory, export_project
from kairos_runtime.reviews import (
    MAX_REVIEW_BYTES,
    archive_files,
    canonical_json,
    validate_review,
    verify_review_baseline,
)

COMMANDS = {"project-export", "changes", "apply", "publish"}


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def read_json(path: Path, *, limit: int = MAX_REVIEW_BYTES) -> dict:
    try:
        with _directory(path.absolute().parent) as parent:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, "rb") as stream:
                meta = os.fstat(stream.fileno())
                if not stat.S_ISREG(meta.st_mode) or meta.st_size > limit:
                    raise ValueError("JSON file type or size limit invalid")
                raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("JSON file size limit exceeded")
        value = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(value, dict):
            raise ValueError("JSON object required")
        return value
    except (OSError, UnicodeError, RecursionError) as exc:
        raise ValueError("cannot read valid JSON file") from exc


def _evidence_message(receipt: dict) -> str:
    evidence = {k: v for k, v in receipt.items() if k != "commit"}
    digest = hashlib.sha256(canonical_json(evidence)).hexdigest()
    return f"Apply runtime review {receipt['review_id']}\n\nKairos-delivery: {digest}"


def apply_review(
    *,
    repo: Path,
    bundle: Path,
    approve: str,
    branch: str,
    worktree: Path,
    tests: list[str],
    receipt: Path,
) -> dict:
    review = validate_review(read_json(bundle))
    if approve != review["review_id"]:
        raise ValueError("approval must exactly match the review ID")
    if review["base_commit"] is None:
        raise ValueError("apply requires a Git base commit")
    if not review["changes"]:
        raise ValueError("review has no changes to commit")
    argv_tests = [shlex.split(value) for value in tests]
    if not argv_tests or any(
        not argv or not argv[0] or any("\x00" in value for value in argv) for argv in argv_tests
    ):
        raise ValueError("at least one nonempty test argv is required")
    repo, worktree, receipt = ops.directory(repo), ops.new_path(worktree), ops.new_path(receipt)
    ops.check_branch(repo, branch)
    ops.check_worktree(repo, worktree)
    if receipt.is_relative_to(worktree):
        raise ValueError("receipt must be outside the new worktree")
    if ops.text_git(repo, "branch", "--list", branch):
        raise ValueError("branch already exists; choose a new branch")
    with tempfile.TemporaryDirectory(prefix="kairos-delivery-") as scratch:
        exported = export_project(repo, review["base_commit"], Path(scratch) / "catalog")
        baseline = snapshot_project(Path(exported["cwd"]), include_all=True)
        verify_review_baseline(review, baseline)
        expected = archive_files(baseline)
    for change in review["changes"]:
        if change["kind"] == "delete":
            del expected[change["path"]]
        else:
            expected[change["path"]] = (
                base64.b64decode(change["content_base64"], validate=True),
                change["new_mode"],
            )
    ops.git(repo, "worktree", "add", "-b", branch, "--", str(worktree), review["base_commit"])
    ops.apply_files(worktree, review["changes"])
    ops.verify_files(worktree, expected)
    tree = ops.stage_review(worktree, review["changes"])
    ops.verify_tree(worktree, tree, expected)
    results = [ops.run_test(worktree, argv) for argv in argv_tests]
    ops.verify_files(worktree, expected)
    if (
        ops.text_git(worktree, "rev-parse", "HEAD") != review["base_commit"]
        or ops.text_git(worktree, "symbolic-ref", "--short", "HEAD") != branch
        or ops.text_git(worktree, "write-tree") != tree
    ):
        raise ValueError("tests changed Git HEAD, branch or index; inspect retained worktree")
    ops.verify_tree(worktree, tree, expected)
    result = {
        "schema_version": 1,
        "review_id": review["review_id"],
        "baseline_fingerprint": review["baseline_fingerprint"],
        "base_commit": review["base_commit"],
        "worktree": str(worktree),
        "repo": str(repo),
        "branch": branch,
        "tree": tree,
        "tests": results,
        "changes": [{"path": c["path"], "kind": c["kind"]} for c in review["changes"]],
    }
    if len(canonical_json({**result, "commit": "0" * 64})) > MAX_REVIEW_BYTES:
        raise ValueError("receipt evidence size limit exceeded; shorten test argv")
    ops.git(worktree, "commit", "--no-gpg-sign", "-m", _evidence_message(result))
    result["commit"] = ops.text_git(worktree, "rev-parse", "HEAD")
    ops.write_new(receipt, canonical_json(result))
    return result


async def run_delivery(*, command: str, args, client) -> dict:
    if command == "project-export":
        return export_project(Path(args.repo), args.revision, Path(args.catalog))
    if command == "changes":
        destination = ops.new_path(Path(args.output))
        review = validate_review(await client.changes(args.session))
        ops.write_new(destination, canonical_json(review))
        return {"review_id": review["review_id"], "output": str(destination)}
    if command == "apply":
        return apply_review(
            repo=Path(args.repo),
            bundle=Path(args.bundle),
            approve=args.approve,
            branch=args.branch,
            worktree=Path(args.worktree),
            tests=args.test,
            receipt=Path(args.receipt),
        )
    if command == "publish":
        return publish_review(receipt=Path(args.receipt), title=args.title, base=args.base)
    raise ValueError("unsupported delivery command")


_RECEIPT_FIELDS = {
    "schema_version",
    "review_id",
    "baseline_fingerprint",
    "base_commit",
    "worktree",
    "repo",
    "branch",
    "tree",
    "tests",
    "changes",
    "commit",
}


def _validate_receipt(value: dict) -> dict:  # noqa: PLR0912 -- explicit strict receipt fields
    import re

    from kairos_runtime.reviews import safe_path

    if (
        value.keys() != _RECEIPT_FIELDS
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError("receipt schema invalid")
    for field in ("review_id", "baseline_fingerprint", "base_commit", "tree", "commit"):
        pattern = (
            r"[0-9a-f]{64}"
            if field in {"review_id", "baseline_fingerprint"}
            else r"(?:[0-9a-f]{40}|[0-9a-f]{64})"
        )
        if not isinstance(value[field], str) or not re.fullmatch(pattern, value[field]):
            raise ValueError("receipt digest invalid")
    for field in ("repo", "worktree", "branch"):
        item = value[field]
        if not isinstance(item, str) or not item or any(ord(c) < 32 for c in item):
            raise ValueError("receipt path or branch invalid")
    for field in ("repo", "worktree"):
        if str(Path(os.path.abspath(value[field]))) != value[field]:
            raise ValueError("receipt requires canonical absolute paths")
    tests = value["tests"]
    if not isinstance(tests, list) or not tests:
        raise ValueError("receipt requires successful tests")
    for test in tests:
        if (
            not isinstance(test, dict)
            or test.keys() != {"argv", "returncode"}
            or type(test["returncode"]) is not int
            or test["returncode"] != 0
        ):
            raise ValueError("receipt test result invalid")
        argv = test["argv"]
        if (
            not isinstance(argv, list)
            or not argv
            or not argv[0]
            or any(not isinstance(arg, str) or "\x00" in arg for arg in argv)
        ):
            raise ValueError("receipt test argv invalid")
    changes = value["changes"]
    if not isinstance(changes, list) or not changes:
        raise ValueError("receipt changes invalid")
    paths = []
    for change in changes:
        if (
            not isinstance(change, dict)
            or change.keys() != {"path", "kind"}
            or change["kind"] not in ("add", "modify", "delete")
        ):
            raise ValueError("receipt change invalid")
        paths.append(safe_path(change["path"]))
    if paths != sorted(set(paths)):
        raise ValueError("receipt changes unsorted or duplicated")
    return value


def _verify_receipt_state(value: dict) -> Path:
    repo, worktree = ops.directory(Path(value["repo"])), ops.directory(Path(value["worktree"]))
    ops.check_branch(repo, value["branch"])
    ops.check_worktree(repo, worktree)
    if ops.text_git(
        repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
    ) != ops.text_git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise ValueError("receipt repository and worktree do not match")
    if (
        ops.text_git(worktree, "symbolic-ref", "--short", "HEAD") != value["branch"]
        or ops.text_git(worktree, "rev-parse", "HEAD") != value["commit"]
    ):
        raise ValueError("receipt commit or branch drifted; do not publish")
    if (
        ops.text_git(worktree, "rev-parse", "HEAD^{tree}") != value["tree"]
        or ops.text_git(worktree, "rev-list", "--parents", "-n", "1", "HEAD")
        != value["commit"] + " " + value["base_commit"]
    ):
        raise ValueError("receipt tree or parent commit invalid")
    if ops.text_git(worktree, "show", "-s", "--format=%B", "HEAD") != _evidence_message(value):
        raise ValueError("receipt evidence differs from committed evidence")
    if ops.git(worktree, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("worktree is dirty; restore the tested commit before publication")
    ops.verify_files(worktree, ops.tree_files(worktree, value["tree"]))
    return worktree


def _pr_url(value: object) -> str:
    from urllib.parse import urlsplit

    if not isinstance(value, str):
        raise ValueError("gh returned an invalid PR URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
        or "/pull/" not in parsed.path
    ):
        raise ValueError("gh returned an invalid PR URL")
    return value


def publish_review(*, receipt: Path, title: str, base: str) -> dict:
    value = _validate_receipt(read_json(receipt))
    worktree = _verify_receipt_state(value)
    ops.check_branch(worktree, base)
    if (
        not isinstance(title, str)
        or not title.strip()
        or len(title) > 256
        or any(ord(c) < 32 for c in title)
    ):
        raise ValueError("PR title invalid")
    branch = value["branch"]
    ref = "refs/heads/" + branch
    origin = ops.text_git(worktree, "remote", "get-url", "--all", "origin")
    push_origin = ops.text_git(worktree, "remote", "get-url", "--push", "--all", "origin")
    if not origin or "\n" in origin or push_origin != origin:
        raise ValueError("origin must have one matching fetch and push destination")
    remote = ops.text_git(worktree, "ls-remote", "--heads", "origin", ref)
    if remote and remote != value["commit"] + "\t" + ref:
        raise ValueError("origin branch differs from receipt; choose a new reviewed branch")
    target = _origin_repository(worktree, origin)
    # Explicit immutable source OID and destination; never rely on push.default or force.
    try:
        ops.git(
            worktree,
            "push",
            "--no-force",
            "--no-mirror",
            "--no-follow-tags",
            "--",
            "origin",
            value["commit"] + ":" + ref,
        )
    except ValueError as exc:
        raise ValueError(
            "Git push failed; verify origin and operator access, then retry publish"
        ) from exc
    raw = ops.gh(
        worktree,
        "pr",
        "list",
        "--head",
        branch,
        "--state",
        "open",
        "--repo",
        target,
        "--json",
        "isDraft,isCrossRepository,headRefOid,headRefName,baseRefName,url",
    )
    try:
        existing = json.loads(raw, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(
            "gh returned invalid PR metadata; inspect draft state before retry"
        ) from exc
    if not isinstance(existing, list):
        raise ValueError("gh returned invalid PR metadata")
    if existing:
        if len(existing) != 1 or not isinstance(existing[0], dict):
            raise ValueError("existing PR is ambiguous")
        pr = existing[0]
        if (
            pr.get("isDraft") is not True
            or pr.get("isCrossRepository") is not False
            or pr.get("headRefOid") != value["commit"]
            or pr.get("headRefName") != branch
            or pr.get("baseRefName") != base
        ):
            raise ValueError("existing PR does not match the tested draft commit")
        return {
            "url": _pr_url(pr.get("url")),
            "commit": value["commit"],
            "review_id": value["review_id"],
        }
    body = "\n".join(
        [
            "Runtime delivery reviewed and explicitly approved by the operator.",
            "",
            "Review ID: " + value["review_id"],
            "Baseline fingerprint: " + value["baseline_fingerprint"],
            "Base commit: " + value["base_commit"],
            "Commit: " + value["commit"],
            "Tree: " + value["tree"],
            "",
            "Changes:",
            *["- " + json.dumps(c, ensure_ascii=True, sort_keys=True) for c in value["changes"]],
            "",
            "Successful test argv/results:",
            *["- " + json.dumps(t, ensure_ascii=True, sort_keys=True) for t in value["tests"]],
            "",
        ]
    )
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", prefix="kairos-pr-", suffix=".md"
    ) as output:
        output.write(body)
        output.flush()
        url = (
            ops.gh(
                worktree,
                "pr",
                "create",
                "--draft",
                "--repo",
                target,
                "--title",
                title,
                "--head",
                branch,
                "--base",
                base,
                "--body-file",
                output.name,
            )
            .decode("utf-8")
            .strip()
        )
    return {"url": _pr_url(url), "commit": value["commit"], "review_id": value["review_id"]}


def _origin_repository(worktree: Path, origin: str) -> str:
    from urllib.parse import urlsplit

    raw = ops.gh(worktree, "repo", "view", "--json", "url", "--", origin)
    try:
        metadata = json.loads(raw, object_pairs_hook=_unique_object)
        url = metadata["url"]
        parsed = urlsplit(url)
        parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or len(parts) != 2
            or not all(parts)
            or parsed.query
            or parsed.fragment
            or any(c.isspace() for c in url)
        ):
            raise ValueError("invalid repository URL")
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise ValueError("gh could not resolve the configured origin repository") from exc
    return parsed.netloc + "/" + "/".join(parts)
