"""Host-only Git and filesystem operations for approved runtime delivery.

All subprocesses use argv, disabled Git hooks/signing, bounded capture and no
shell. Directories are opened without following links. The operator owns these
repositories and must keep other writers out while applying or publishing.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

from kairos_runtime.docker_backend.archives import MAX_CONTENT_BYTES, MAX_ENTRIES
from kairos_runtime.projects import _directory, _git
from kairos_runtime.reviews import safe_path


def git(repo: Path, *args: str, limit: int = 4 * 1024 * 1024) -> bytes:
    return _git(
        repo,
        "-c",
        "core.hooksPath=/dev/null",
        "-c",
        "commit.gpgSign=false",
        "-c",
        "core.fsmonitor=false",
        "--literal-pathspecs",
        *args,
        limit=limit,
    )


def text_git(repo: Path, *args: str) -> str:
    return git(repo, *args).decode("utf-8").strip()


def directory(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    with _directory(path):
        pass
    return path


def new_path(path: Path) -> Path:
    path = Path(os.path.abspath(path))
    with _directory(path.parent) as fd:
        try:
            os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return path
    raise ValueError("output path already exists; choose a new path")


def write_new(path: Path, data: bytes, mode: int = 0o600) -> None:
    with _directory(path.parent) as fd:
        try:
            target = os.open(
                path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd
            )
            with os.fdopen(target, "wb") as stream:
                os.fchmod(stream.fileno(), mode)
                stream.write(data)
        except OSError as exc:
            raise ValueError("cannot create output; choose a new writable path") from exc


def check_branch(repo: Path, branch: str) -> None:
    if not isinstance(branch, str) or not branch or branch.startswith("-"):
        raise ValueError("invalid branch name")
    if text_git(repo, "check-ref-format", "--branch", branch) != branch:
        raise ValueError("invalid branch name")


def check_worktree(repo: Path, worktree: Path) -> None:
    if any(
        (parent / "project.json").exists() and (parent / "workspace").is_dir()
        for parent in worktree.parents
    ):
        raise ValueError("worktree must be outside exported project versions")
    forbidden = [
        repo,
        Path(text_git(repo, "rev-parse", "--show-toplevel")),
        Path(text_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")),
    ]
    if any(worktree.is_relative_to(path) for path in forbidden) or any(
        p.casefold() == ".git" for p in worktree.parts
    ):
        raise ValueError("worktree must be outside the source repository and Git directories")


def apply_files(worktree: Path, changes: list[dict]) -> None:
    # Remove old files first, then empty parents, to support both transition directions.
    for change in changes:
        if change["kind"] == "add":
            continue
        target = worktree / change["path"]
        with _directory(target.parent) as fd:
            os.unlink(target.name, dir_fd=fd)
        parent = target.parent
        while parent != worktree:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
    import base64

    for change in changes:
        if change["kind"] == "delete":
            continue
        target = worktree / change["path"]
        with _directory(target.parent, create=True):
            pass
        write_new(
            target, base64.b64decode(change["content_base64"], validate=True), change["new_mode"]
        )


def verify_files(worktree: Path, expected: dict[str, tuple[bytes, int]]) -> None:
    listed = git(worktree, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    paths = {safe_path(raw.decode("utf-8")) for raw in listed.split(b"\0") if raw}
    for path in paths | expected.keys():
        target = worktree / path
        if (
            path not in expected
            and target.is_dir()
            and not target.is_symlink()
            and any(name.startswith(path + "/") for name in expected)
        ):
            continue
        try:
            with _directory(target.parent) as parent:
                fd = os.open(
                    target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
                )
                with os.fdopen(fd, "rb") as stream:
                    metadata = os.fstat(stream.fileno())
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise ValueError("worktree contains a link or special file")
                    if path not in expected:
                        raise ValueError("worktree contains unexpected changes")
                    data, mode = expected[path]
                    if (
                        (493 if metadata.st_mode & 0o111 else 420) != mode
                        or metadata.st_size != len(data)
                        or stream.read(len(data) + 1) != data
                    ):
                        raise ValueError("worktree does not match approved content or modes")
        except (OSError, ValueError) as exc:
            # Old tracked paths can disappear during a reviewed transition.
            if path not in expected and not target.exists() and not target.is_symlink():
                continue
            raise ValueError(
                "worktree does not match approved files; inspect retained worktree"
            ) from exc


def stage_review(worktree: Path, changes: list[dict]) -> str:
    with tempfile.NamedTemporaryFile() as paths:
        paths.write(b"".join(change["path"].encode() + b"\0" for change in changes))
        paths.flush()
        git(worktree, "add", "--all", "--pathspec-from-file=" + paths.name, "--pathspec-file-nul")
    return text_git(worktree, "write-tree")


def verify_tree(worktree: Path, tree: str, expected: dict[str, tuple[bytes, int]]) -> None:
    if tree_files(worktree, tree) != expected:
        raise ValueError("Git tree differs from approved content; check Git filters")


def run_test(worktree: Path, argv: list[str]) -> dict:
    try:
        result = subprocess.run(  # noqa: S603 -- explicit operator-authorized argv
            argv,
            cwd=worktree,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("test could not run or timed out; inspect retained worktree") from exc
    if result.returncode:
        raise ValueError(f"test failed (exit {result.returncode}); inspect retained worktree")
    return {"argv": argv, "returncode": 0}


def gh(worktree: Path, *args: str) -> bytes:
    # Discard diagnostics because GitHub CLI can echo remote URLs or credentials.
    env = {key: value for key, value in os.environ.items() if key not in {"GH_REPO", "GH_DEBUG"}}
    try:
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(  # noqa: S603 -- fixed host CLI with structured options
                ["gh", *args],  # noqa: S607 -- trusted host CLI on PATH
                cwd=worktree,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=120,
                check=False,
            )
            output.seek(0)
            data = output.read(65537)
        if result.returncode or len(data) > 65536:
            raise ValueError(
                "gh command failed; verify operator authentication and draft PR state, then retry publish"
            )
        return data
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            "gh unavailable or timed out; verify installation and operator authentication"
        ) from exc


def tree_files(worktree: Path, tree: str) -> dict[str, tuple[bytes, int]]:
    records = git(worktree, "ls-tree", "-rz", "--full-tree", tree).split(b"\0")[:-1]
    if len(records) > MAX_ENTRIES:
        raise ValueError("Git tree entry limit exceeded")
    result, total = {}, 0
    for record in records:
        metadata, name = record.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        if kind != b"blob" or mode not in (b"100644", b"100755"):
            raise ValueError("Git tree contains links or special files")
        data = git(worktree, "cat-file", "blob", oid.decode(), limit=MAX_CONTENT_BYTES - total)
        total += len(data)
        result[safe_path(name.decode())] = (data, 493 if mode == b"100755" else 420)
    return result
