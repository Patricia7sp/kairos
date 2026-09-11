"""Bounded, content-addressed workspace reviews, without applying any changes.

Example: ``bundle = build_review('session-1', baseline_tar, checkpoint_tar)``;
``validate_review(bundle)`` verifies the complete package before approval/use.
``workspace_fingerprint(tar_bytes)`` ignores empty directories and tar metadata.
Only regular files and directories are accepted; Git internals, control characters
and ambiguous paths are forbidden. Modes normalize to 0644/0755. Packages are at
most 512 KiB including their ID; large changes fail rather than being truncated.
An ID proves package integrity, not authorship or correspondence to a Git tree.
"""

from __future__ import annotations

import base64
import binascii
import difflib
import hashlib
import io
import json
import re
import tarfile

from kairos_runtime.docker_backend.archives import MAX_ENTRIES, validate_archive

MAX_REVIEW_BYTES = 512 * 1024
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_FIELDS = {
    "schema_version",
    "session_id",
    "base_commit",
    "baseline_fingerprint",
    "checkpoint_digest",
    "changes",
    "diff",
    "review_id",
}
_CHANGE_FIELDS = {
    "path",
    "kind",
    "old_sha256",
    "new_sha256",
    "old_mode",
    "new_mode",
    "content_base64",
}


def canonical_json(value: object) -> bytes:
    """Encode the stable JSON representation used for fingerprints and approvals."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("ascii")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("review JSON invalid") from exc


def safe_path(path: object) -> str:
    """Validate a canonical relative file path, excluding Git administration."""
    if (
        not isinstance(path, str)
        or not path
        or len(path.encode("utf-8", "surrogatepass")) > 4096
        or "\\" in path
        or any(ord(c) < 32 or ord(c) == 127 for c in path)
        or any(0xD800 <= ord(c) <= 0xDFFF for c in path)
        or any(part in {"", ".", ".."} or part.casefold() == ".git" for part in path.split("/"))
    ):
        raise ValueError("review path invalid")
    return path


def archive_files(blob: bytes) -> dict[str, tuple[bytes, int]]:
    """Read validated, bounded archive files without extracting to disk."""
    validate_archive(blob)
    files = {}
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as archive:
        for member in archive:
            safe_path(member.name)
            if member.isreg():
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("archive file unreadable")
                files[member.name] = (source.read(), 493 if member.mode & 0o111 else 420)
    return files


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fingerprint(files: dict[str, tuple[bytes, int]]) -> str:
    return _sha(
        canonical_json(
            [
                {"path": path, "sha256": _sha(data), "mode": mode}
                for path, (data, mode) in sorted(files.items())
            ]
        )
    )


def workspace_fingerprint(archive: bytes) -> str:
    """Hash sorted path/content/normalized-mode entries of a validated archive."""
    return _fingerprint(archive_files(archive))


def _diff(path: str, old: tuple[bytes, int] | None, new: tuple[bytes, int] | None) -> str:
    old_data, old_mode = old if old is not None else (b"", None)
    new_data, new_mode = new if new is not None else (b"", None)
    output = f"diff -- {json.dumps(path, ensure_ascii=True)}\n"
    if old_mode != new_mode:
        output += f"mode {old_mode if old_mode is not None else '-'} -> {new_mode if new_mode is not None else '-'}\n"
    if old_data != new_data:
        try:
            before, after = old_data.decode("utf-8"), new_data.decode("utf-8")
            if "\x00" in before or "\x00" in after:
                raise UnicodeError("binary NUL")
        except UnicodeError:
            output += "Binary files differ\n"
        else:
            lines = difflib.unified_diff(
                before.splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="a/" + path if old else "/dev/null",
                tofile="b/" + path if new else "/dev/null",
            )
            for line in lines:
                output += line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
    return output


def build_review(
    session_id: str, baseline: bytes, checkpoint: bytes, *, base_commit: str | None = None
) -> dict:
    """Build and validate a complete review; reject artifacts above the size limit."""
    before, after = archive_files(baseline), archive_files(checkpoint)
    changes, diff = [], []
    for path in sorted(before.keys() | after.keys()):
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        changes.append(
            {
                "path": path,
                "kind": "add" if old is None else "delete" if new is None else "modify",
                "old_sha256": _sha(old[0]) if old else None,
                "new_sha256": _sha(new[0]) if new else None,
                "old_mode": old[1] if old else None,
                "new_mode": new[1] if new else None,
                "content_base64": base64.b64encode(new[0]).decode("ascii") if new else None,
            }
        )
        diff.append(_diff(path, old, new))
    bundle = {
        "schema_version": 1,
        "session_id": session_id,
        "base_commit": base_commit,
        "baseline_fingerprint": _fingerprint(before),
        "checkpoint_digest": _sha(checkpoint),
        "changes": changes,
        "diff": "".join(diff),
    }
    bundle["review_id"] = _sha(canonical_json(bundle))
    return validate_review(bundle)


def _hash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _validate_change(change: object) -> str:  # noqa: PLR0912 -- explicit strict schema cases
    if not isinstance(change, dict) or change.keys() != _CHANGE_FIELDS:
        raise ValueError("review change schema invalid")
    path = safe_path(change["path"])
    kind = change["kind"]
    if kind not in ("add", "modify", "delete"):
        raise ValueError("review change kind invalid")
    for side, absent in (("old", kind == "add"), ("new", kind == "delete")):
        sha, mode = change[f"{side}_sha256"], change[f"{side}_mode"]
        if absent:
            if sha is not None or mode is not None:
                raise ValueError("review absent file metadata invalid")
        elif not _hash(sha) or type(mode) is not int or mode not in (420, 493):
            raise ValueError("review file metadata invalid")
    content = change["content_base64"]
    if kind == "delete":
        if content is not None:
            raise ValueError("review deletion content invalid")
    else:
        if not isinstance(content, str):
            raise ValueError("review content invalid")
        try:
            data = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("review content base64 invalid") from exc
        if base64.b64encode(data).decode("ascii") != content or _sha(data) != change["new_sha256"]:
            raise ValueError("review content hash invalid")
    if (
        kind == "modify"
        and change["old_sha256"] == change["new_sha256"]
        and change["old_mode"] == change["new_mode"]
    ):
        raise ValueError("review unchanged file invalid")
    return path


def validate_review(bundle: dict) -> dict:
    """Validate strict schema, paths, content, canonical size and approval digest.

    The baseline and checkpoint must be checked separately by consumers with
    access to those sources; neither can be reconstructed from a change package.
    """
    if not isinstance(bundle, dict) or bundle.keys() != _FIELDS:
        raise ValueError("review schema invalid")
    if len(canonical_json(bundle)) > MAX_REVIEW_BYTES:
        raise ValueError("review size limit exceeded")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != 1:
        raise ValueError("review schema version invalid")
    session = bundle["session_id"]
    if (
        not isinstance(session, str)
        or not session
        or len(session) > 256
        or any(ord(c) < 32 for c in session)
    ):
        raise ValueError("review session invalid")
    commit = bundle["base_commit"]
    if commit is not None and (not isinstance(commit, str) or not _COMMIT.fullmatch(commit)):
        raise ValueError("review base commit invalid")
    if not all(
        _hash(bundle[field]) for field in ("baseline_fingerprint", "checkpoint_digest", "review_id")
    ):
        raise ValueError("review digest invalid")
    if not isinstance(bundle["diff"], str):
        raise ValueError("review diff invalid")
    changes = bundle["changes"]
    if not isinstance(changes, list) or len(changes) > MAX_ENTRIES * 2:
        raise ValueError("review changes invalid")
    paths = [_validate_change(change) for change in changes]
    if paths != sorted(set(paths)):
        raise ValueError("review paths duplicated or unsorted")
    for side, absent in (("old", "add"), ("new", "delete")):
        present = {change["path"] for change in changes if change["kind"] != absent}
        if any(
            "/".join(path.split("/")[:i]) in present
            for path in present
            for i in range(1, len(path.split("/")))
        ):
            raise ValueError(f"review {side} file used as directory")
    payload = {key: value for key, value in bundle.items() if key != "review_id"}
    if _sha(canonical_json(payload)) != bundle["review_id"]:
        raise ValueError("review approval digest mismatch")
    return bundle


def verify_review_baseline(bundle: dict, baseline: bytes) -> None:
    """Verify old files and the displayed diff against a trusted baseline archive.

    Example: ``verify_review_baseline(bundle, snapshot_project(exported_workspace))``
    before approved application. This also rejects additions over existing files
    and file/directory conflicts with unchanged baseline paths. Raw checkpoint
    digest remains provenance metadata: a tar cannot be reconstructed byte-for-byte
    from normalized files, so callers needing that proof must retain the checkpoint.
    """
    validate_review(bundle)
    before = archive_files(baseline)
    if _fingerprint(before) != bundle["baseline_fingerprint"]:
        raise ValueError("review baseline fingerprint mismatch")
    after, diff = before.copy(), []
    for change in bundle["changes"]:
        path = change["path"]
        old = before.get(path)
        if change["kind"] == "add":
            if old is not None:
                raise ValueError("review addition already exists in baseline")
        elif old is None or _sha(old[0]) != change["old_sha256"] or old[1] != change["old_mode"]:
            raise ValueError("review old file does not match baseline")
        if change["kind"] == "delete":
            new = None
            del after[path]
        else:
            new = (base64.b64decode(change["content_base64"], validate=True), change["new_mode"])
            after[path] = new
        diff.append(_diff(path, old, new))
    if any(
        "/".join(path.split("/")[:i]) in after
        for path in after
        for i in range(1, len(path.split("/")))
    ):
        raise ValueError("review resulting file used as directory")
    if "".join(diff) != bundle["diff"]:
        raise ValueError("review diff does not match approved file contents")
