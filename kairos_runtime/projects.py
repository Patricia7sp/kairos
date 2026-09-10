"""Immutable, operator-exported Git source versions for exact workspace selection.

Example: ``export_project(Path('/src/app'), 'main', Path('/catalog'))`` returns
manifest metadata plus ``cwd``. ``discover_projects(('/catalog',))`` verifies all
published versions; ``project_metadata(Path(cwd))`` verifies one exact workspace.
Legacy project directories return None. Corrupt catalogs fail closed.

Layout: <catalog>/<full commit>/workspace and sibling project.json. Export reads
Git objects, never working-tree files or checkout hooks. Tracked links, submodules
and snapshot-excluded paths fail explicitly. Published contents are never repaired.
Atomic publication uses Linux renameat2(RENAME_NOREPLACE); catalogs are trusted
operator storage, not a defense against an operator rewriting source and manifest.
"""

from __future__ import annotations

import ctypes
import errno
import io
import json
import os
import re
import secrets
import selectors
import shutil
import stat
import subprocess
import tarfile
import time
from contextlib import contextmanager
from pathlib import Path

from kairos_runtime.docker_backend.archives import MAX_CONTENT_BYTES, MAX_ENTRIES
from kairos_runtime.experimental.snapshot import EXCLUDED, snapshot_project
from kairos_runtime.reviews import archive_files, canonical_json, safe_path, workspace_fingerprint

_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_FIELDS = {"schema_version", "revision", "name", "baseline_fingerprint"}
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _directory(path: Path, *, create: bool = False):
    """Open every parent without following links, optionally creating directories."""
    fd = os.open("/", _DIR_FLAGS)
    try:
        for part in Path(os.path.abspath(path)).parts[1:]:
            created = False
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
            following = os.open(part, _DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = following
            if created:
                os.fchmod(fd, 0o755)
        yield fd
    except OSError as exc:
        raise ValueError("project directory invalid or inaccessible") from exc
    finally:
        os.close(fd)


def _git(repo: Path, *args: str, limit: int) -> bytes:
    """Bound stdout in memory and terminate on output overflow or timeout."""
    try:
        with (
            subprocess.Popen(  # noqa: S603 -- argv only, revision separated by caller
                ["git", "-C", str(repo), *args],  # noqa: S607 -- trusted host Git on PATH
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            ) as process,
            selectors.DefaultSelector() as selector,
        ):
            selector.register(process.stdout, selectors.EVENT_READ)
            output = bytearray()
            deadline = time.monotonic() + 30
            try:
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("Git export timed out")
                    if not selector.select(remaining):
                        raise ValueError("Git export timed out")
                    chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                    if not chunk:
                        selector.unregister(process.stdout)
                    output.extend(chunk)
                    if len(output) > limit:
                        raise ValueError("Git export output limit exceeded")
                if process.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                    raise ValueError("Git export failed; verify repository and revision")
            except BaseException:
                process.kill()
                process.wait()
                raise
            return bytes(output)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git export failed or timed out") from exc


def _source_path(path: str) -> str:
    safe_path(path)
    if any(part in EXCLUDED or part.startswith(".env") for part in path.split("/")):
        raise ValueError("tracked path excluded by snapshot policy")
    return path


def _git_archive(repo: Path, commit: str) -> bytes:
    tree = _git(repo, "ls-tree", "-rz", "--full-tree", commit, limit=MAX_ENTRIES * (4096 + 100))
    records = tree.split(b"\x00")[:-1]
    if len(records) > MAX_ENTRIES:
        raise ValueError("project entry limit exceeded")
    output, total = io.BytesIO(), 0
    with tarfile.open(fileobj=output, mode="w") as archive:
        for record in records:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.split(b" ")
            if kind != b"blob" or mode not in (b"100644", b"100755"):
                raise ValueError("tracked links, submodules or special files forbidden")
            try:
                path = _source_path(raw_path.decode("utf-8"))
            except UnicodeError as exc:
                raise ValueError("tracked path must be UTF-8") from exc
            content = _git(
                repo, "cat-file", "blob", oid.decode("ascii"), limit=MAX_CONTENT_BYTES - total
            )
            total += len(content)
            member = tarfile.TarInfo(path)
            member.mode = 493 if mode == b"100755" else 420
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    blob = output.getvalue()
    archive_files(blob)
    return blob


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("project manifest duplicate key")
        result[key] = value
    return result


def _manifest(workspace: Path, *, baseline: bytes | None = None) -> dict:
    with _directory(workspace.parent) as version_fd:
        if set(os.listdir(version_fd)) != {"workspace", "project.json"}:
            raise ValueError("project version layout invalid")
        fd = os.open("project.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=version_fd)
        with os.fdopen(fd, "rb") as source:
            metadata = os.fstat(source.fileno())
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_nlink != 1
                or metadata.st_size > 4096
            ):
                raise ValueError("project manifest invalid")
            raw = source.read(4097)
    try:
        manifest = json.loads(raw, object_pairs_hook=_unique_object)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("project manifest JSON invalid") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.keys() != _MANIFEST_FIELDS
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or not isinstance(manifest["revision"], str)
        or not _COMMIT.fullmatch(manifest["revision"])
        or manifest["revision"] != workspace.parent.name
        or not isinstance(manifest["name"], str)
        or not manifest["name"]
        or "/" in manifest["name"]
        or "\\" in manifest["name"]
        or not isinstance(manifest["baseline_fingerprint"], str)
        or not _HASH.fullmatch(manifest["baseline_fingerprint"])
    ):
        raise ValueError("project manifest schema invalid")
    with _directory(workspace):
        pass
    blob = baseline if baseline is not None else snapshot_project(workspace, include_all=True)
    for path in archive_files(blob):
        _source_path(path)
    if workspace_fingerprint(blob) != manifest["baseline_fingerprint"]:
        raise ValueError("project baseline fingerprint mismatch")
    return {**manifest, "cwd": str(workspace)}


def project_metadata(workspace: Path, *, baseline: bytes | None = None) -> dict | None:
    """Verify a catalog workspace, or return None for a legacy directory.

    When baseline is supplied, verify that captured archive against the trusted
    sibling manifest without recapturing source. Directory and manifest no-follow
    checks remain active; callers must capture the archive from this workspace.
    """
    workspace = Path(os.path.abspath(workspace))
    if workspace.name != "workspace":
        return None
    with _directory(workspace.parent) as fd:
        try:
            os.stat("project.json", dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
    return _manifest(workspace, baseline=baseline)


def discover_projects(catalogs: tuple[str, ...]) -> list[dict]:
    """Discover strictly verified immutable versions, sorted by catalog and commit."""
    versions, seen = [], set()
    for catalog in sorted(set(catalogs)):
        root = Path(os.path.abspath(catalog))
        with _directory(root) as fd:
            names = sorted(os.listdir(fd))
            if len(names) > MAX_ENTRIES:
                raise ValueError("project catalog entry limit exceeded")
            for name in names:
                if name.startswith(".staging-"):
                    continue
                if not _COMMIT.fullmatch(name):
                    raise ValueError("project catalog version name invalid")
                workspace = root / name / "workspace"
                result = _manifest(workspace)
                if result["cwd"] not in seen:
                    versions.append(result)
                    seen.add(result["cwd"])
    return versions


def _write_workspace(workspace: Path, blob: bytes) -> None:
    with _directory(workspace) as fd:
        os.fchmod(fd, 0o755)
    for path, (data, mode) in archive_files(blob).items():
        target = workspace / path
        with _directory(target.parent, create=True) as fd:
            destination = os.open(
                target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=fd
            )
            with os.fdopen(destination, "wb") as stream:
                stream.write(data)
                os.fchmod(stream.fileno(), mode)


def _publish(fd: int, staging: str, commit: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(fd, os.fsencode(staging), fd, os.fsencode(commit), 1) != 0:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise FileExistsError(code, "project already published")
        raise OSError(code, "atomic project publication failed")


def export_project(repo: Path, revision: str, catalog: Path) -> dict:
    """Export a resolved Git commit atomically; verify instead of replacing repeats."""
    if not isinstance(revision, str) or not revision or "\x00" in revision:
        raise ValueError("project revision invalid")
    repo, catalog = Path(os.path.abspath(repo)), Path(os.path.abspath(catalog))
    commit = (
        _git(repo, "rev-parse", "--verify", "--end-of-options", revision + "^{commit}", limit=100)
        .decode("ascii")
        .strip()
    )
    if not _COMMIT.fullmatch(commit):
        raise ValueError("resolved project commit invalid")
    blob = _git_archive(repo, commit)
    manifest = {
        "schema_version": 1,
        "revision": commit,
        "name": repo.name,
        "baseline_fingerprint": workspace_fingerprint(blob),
    }
    expected = {**manifest, "cwd": str(catalog / commit / "workspace")}
    with _directory(catalog, create=True) as fd:
        if commit in os.listdir(fd):
            if _manifest(catalog / commit / "workspace") != expected:
                raise ValueError("existing project metadata mismatch")
            return expected
        staging = ".staging-" + secrets.token_hex(16)
        os.mkdir(staging, mode=0o700, dir_fd=fd)
        stage = catalog / staging
        try:
            with _directory(stage) as stage_fd:
                os.mkdir("workspace", mode=0o755, dir_fd=stage_fd)
                _write_workspace(stage / "workspace", blob)
                destination = os.open(
                    "project.json",
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o644,
                    dir_fd=stage_fd,
                )
                with os.fdopen(destination, "wb") as stream:
                    stream.write(canonical_json(manifest))
                    os.fchmod(stream.fileno(), 0o644)
                if (
                    workspace_fingerprint(snapshot_project(stage / "workspace", include_all=True))
                    != manifest["baseline_fingerprint"]
                ):
                    raise ValueError("staged project fingerprint mismatch")
                os.fchmod(stage_fd, 0o755)
            try:
                _publish(fd, staging, commit)
            except FileExistsError:
                if _manifest(catalog / commit / "workspace") != expected:
                    raise ValueError("concurrent project publication mismatch") from None
        finally:
            if staging in os.listdir(fd):
                shutil.rmtree(staging, dir_fd=fd)
    if _manifest(catalog / commit / "workspace") != expected:
        raise ValueError("published project verification failed")
    return expected
