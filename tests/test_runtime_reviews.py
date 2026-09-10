import base64
import copy
import hashlib
import io
import json
import tarfile

import pytest

from kairos_runtime.reviews import build_review, validate_review, workspace_fingerprint


def archive(*files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        for path, content, mode in files:
            member = tarfile.TarInfo(path)
            member.mode = mode
            if content is None:
                member.type = tarfile.DIRTYPE
            else:
                member.size = len(content)
            tar.addfile(member, None if content is None else io.BytesIO(content))
    return output.getvalue()


def resign(bundle):
    bundle.pop("review_id", None)
    bundle["review_id"] = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return bundle


def test_review_covers_content_binary_modes_and_deletion():
    baseline = archive(
        ("edit", b"before\n", 0o644),
        ("gone", b"gone", 0o644),
        ("mode", b"same", 0o644),
        ("binary", b"\x00\xff", 0o644),
    )
    checkpoint = archive(
        ("edit", b"after\n", 0o644),
        ("new", b"new", 0o644),
        ("mode", b"same", 0o700),
        ("binary", b"\x00\xfe", 0o644),
    )
    result = build_review("session-1", baseline, checkpoint, base_commit="a" * 40)
    assert validate_review(result) == result
    assert [(c["path"], c["kind"]) for c in result["changes"]] == [
        ("binary", "modify"),
        ("edit", "modify"),
        ("gone", "delete"),
        ("mode", "modify"),
        ("new", "add"),
    ]
    assert result["changes"][2]["content_base64"] is None
    assert result["changes"][3]["new_mode"] == 493
    assert base64.b64decode(result["changes"][4]["content_base64"]) == b"new"
    assert "-before" in result["diff"] and "+after" in result["diff"]
    assert "Binary" in result["diff"] and "mode" in result["diff"]
    assert result["checkpoint_digest"] == hashlib.sha256(checkpoint).hexdigest()


def test_fingerprint_canonical_content_modes_ignore_empty_dirs_and_tar_order():
    first = archive(("z", b"hello", 0o600), ("empty", None, 0o755))
    second = archive(("z", b"hello", 0o644))
    entries = [{"path": "z", "sha256": hashlib.sha256(b"hello").hexdigest(), "mode": 420}]
    expected = hashlib.sha256(
        json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert workspace_fingerprint(first) == workspace_fingerprint(second) == expected
    assert workspace_fingerprint(archive(("z", b"hello", 0o755))) != expected


@pytest.mark.parametrize("path", ["../escape", "/absolute", "a/.git/config", ".git", "a\\b"])
def test_review_rejects_unsafe_archive_paths(path):
    with pytest.raises(ValueError):
        build_review("s", archive(), archive((path, b"bad", 0o644)))


@pytest.mark.parametrize(
    "mutation",
    [
        "id",
        "content",
        "hash",
        "schema",
        "duplicate",
        "kind",
        "mode",
        "null",
        "path",
        "extra",
        "bool",
    ],
)
def test_validator_rejects_tampering_even_with_recomputed_digest(mutation):
    bundle = build_review("s", archive(), archive(("file", b"hello", 0o644)))
    change = bundle["changes"][0]
    if mutation == "id":
        bundle["review_id"] = "0" * 64
    elif mutation == "content":
        change["content_base64"] = base64.b64encode(b"changed").decode()
    elif mutation == "hash":
        change["new_sha256"] = "bad"
    elif mutation == "schema":
        bundle["schema_version"] = 2
    elif mutation == "duplicate":
        bundle["changes"].append(copy.deepcopy(change))
    elif mutation == "kind":
        change["kind"] = "modify"
    elif mutation == "mode":
        change["new_mode"] = 0o777
    elif mutation == "null":
        change["content_base64"] = None
    elif mutation == "path":
        change["path"] = ".git/config"
    elif mutation == "extra":
        bundle["surprise"] = True
    elif mutation == "bool":
        bundle["schema_version"] = True
    if mutation != "id":
        resign(bundle)
    with pytest.raises(ValueError):
        validate_review(bundle)


def test_review_limit_includes_payload_and_final_id():
    with pytest.raises(ValueError, match="limit"):
        build_review("s", archive(), archive(("large", b"x" * 400_000, 0o644)))
    bundle = build_review("s", archive(), archive())
    bundle["diff"] = "x" * (512 * 1024)
    with pytest.raises(ValueError, match="limit"):
        validate_review(resign(bundle))


def test_verify_baseline_rejects_forged_diff_old_content_and_missing_files():
    from kairos_runtime.reviews import verify_review_baseline

    baseline = archive(("file", b"before\n", 0o644))
    bundle = build_review("s", baseline, archive(("file", b"after\n", 0o755)))
    assert verify_review_baseline(bundle, baseline) is None
    for mutation in ("diff", "old_hash", "old_mode", "path"):
        forged = copy.deepcopy(bundle)
        if mutation == "diff":
            forged["diff"] = "No changes; safe to approve"
        elif mutation == "old_hash":
            forged["changes"][0]["old_sha256"] = "0" * 64
        elif mutation == "old_mode":
            forged["changes"][0]["old_mode"] = 493
        else:
            forged["changes"][0]["path"] = "missing"
        with pytest.raises(ValueError):
            verify_review_baseline(resign(forged), baseline)
    with pytest.raises(ValueError):
        verify_review_baseline(bundle, archive(("file", b"other", 0o644)))


def test_verify_baseline_handles_add_delete_and_file_directory_transitions():
    from kairos_runtime.reviews import verify_review_baseline

    baseline = archive(("dir/file", b"old", 0o644), ("file", b"old", 0o644))
    checkpoint = archive(("dir", b"new", 0o644), ("file/child", b"new", 0o644))
    bundle = build_review("s", baseline, checkpoint)
    assert verify_review_baseline(bundle, baseline) is None
    forged = build_review("s", archive(), archive(("file", b"new", 0o644)))
    forged["baseline_fingerprint"] = workspace_fingerprint(baseline)
    with pytest.raises(ValueError):
        verify_review_baseline(resign(forged), baseline)


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE])
def test_review_rejects_archive_links(kind):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as tar:
        member = tarfile.TarInfo("link")
        member.type = kind
        member.linkname = "target"
        tar.addfile(member)
    with pytest.raises(ValueError):
        build_review("s", archive(), output.getvalue())


def test_validator_rejects_unsorted_and_conflicting_changes_and_noncanonical_base64():
    bundle = build_review("s", archive(), archive(("a", b"a", 0o644), ("b", b"b", 0o644)))
    for mutation in ("unsorted", "conflict", "base64", "null_old"):
        forged = copy.deepcopy(bundle)
        if mutation == "unsorted":
            forged["changes"].reverse()
        elif mutation == "conflict":
            forged["changes"][1]["path"] = "a/b"
        elif mutation == "base64":
            forged["changes"][0]["content_base64"] = "YR=="
        else:
            forged["changes"][0]["old_mode"] = 420
        with pytest.raises(ValueError):
            validate_review(resign(forged))


def test_review_size_boundary_counts_final_id():
    bundle = build_review("s", archive(), archive())
    size = len(
        json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    )
    bundle["diff"] = "x" * (512 * 1024 - size)
    assert validate_review(resign(bundle)) == bundle
    bundle["diff"] += "x"
    with pytest.raises(ValueError, match="limit"):
        validate_review(resign(bundle))
