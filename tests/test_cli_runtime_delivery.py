import asyncio
import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from kairos_cli.main import build_parser
from kairos_runtime.experimental.snapshot import snapshot_project
from kairos_runtime.reviews import build_review, canonical_json


def git(repo, *args):
    return (
        subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL)
        .decode()
        .strip()
    )


@pytest.fixture
def delivery(tmp_path):
    repo = tmp_path / "source"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test Operator")
    git(repo, "config", "user.email", "operator@example.invalid")
    for path, data in {
        "edit": b"before\n",
        "gone": b"gone",
        "mode": b"same",
        "binary": b"\x00\xff",
        ".gitignore": b"generated/\n",
        "dir/old": b"old",
        "file": b"old",
    }.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    baseline = snapshot_project(repo)
    commit = git(repo, "rev-parse", "HEAD")
    checkpoint = tmp_path / "checkpoint"
    checkpoint.mkdir()
    for path, data in {
        "edit": b"after\n",
        "new": b"new",
        "mode": b"same",
        "binary": b"\x00\xfe",
        ".gitignore": b"generated/\n",
        "dir": b"new",
        "file/child": b"new",
    }.items():
        target = checkpoint / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (checkpoint / "mode").chmod(0o755)
    bundle = build_review("session-1", baseline, snapshot_project(checkpoint), base_commit=commit)
    bundle_path = tmp_path / "review.json"
    bundle_path.write_bytes(canonical_json(bundle))
    return dict(
        repo=repo,
        bundle=bundle_path,
        approve=bundle["review_id"],
        branch="review/task",
        worktree=tmp_path / "applied",
        tests=[
            shlex.join(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; assert Path('edit').read_text() == 'after\\n'; Path('generated').mkdir(); Path('generated/log').write_text('ok')",
                ]
            )
        ],
        receipt=tmp_path / "receipt.json",
    )


def apply(options):
    from kairos_cli.runtime_delivery import apply_review

    return apply_review(**options)


def test_apply_commits_only_reviewed_content_modes_and_transitions(delivery):
    result = apply(delivery)
    worktree = delivery["worktree"]
    assert git(delivery["repo"], "branch", "--show-current") == "main"
    assert (delivery["repo"] / "edit").read_bytes() == b"before\n"
    assert git(worktree, "rev-parse", "HEAD") == result["commit"]
    assert git(worktree, "rev-parse", "HEAD^") == result["base_commit"]
    assert git(worktree, "status", "--porcelain") == ""
    assert (worktree / "binary").read_bytes() == b"\x00\xfe"
    assert (worktree / "dir").read_bytes() == b"new"
    assert (worktree / "file/child").read_bytes() == b"new"
    assert not (worktree / "gone").exists()
    assert (worktree / "mode").stat().st_mode & 0o777 == 0o755
    assert delivery["receipt"].stat().st_mode & 0o777 == 0o600
    assert json.loads(delivery["receipt"].read_bytes()) == result
    assert result["tests"][0]["argv"][0] == sys.executable
    assert result["tests"][0]["returncode"] == 0


@pytest.mark.parametrize(
    "case",
    [
        "approval",
        "base",
        "null_base",
        "duplicate",
        "oversize",
        "path",
        "branch",
        "existing_branch",
        "existing_worktree",
        "nested_worktree",
        "symlink_parent",
        "receipt_exists",
        "empty_tests",
        "empty_argv",
        "empty_executable",
    ],
)
def test_apply_rejects_before_creating_worktree(delivery, case, tmp_path):  # noqa: PLR0912 -- independent invalid inputs
    if case == "approval":
        delivery["approve"] = "0" * 64
    elif case in {"base", "null_base", "path"}:
        value = json.loads(delivery["bundle"].read_bytes())
        if case == "path":
            value["changes"][0]["path"] = ".git/config"
        else:
            value["base_commit"] = None if case == "null_base" else "a" * 40
        value.pop("review_id")
        value["review_id"] = hashlib.sha256(canonical_json(value)).hexdigest()
        delivery["approve"] = value["review_id"]
        delivery["bundle"].write_bytes(canonical_json(value))
    elif case == "duplicate":
        raw = delivery["bundle"].read_bytes()
        delivery["bundle"].write_bytes(b'{"schema_version":1,' + raw[1:])
    elif case == "oversize":
        delivery["bundle"].write_bytes(b" " * (512 * 1024 + 1))
    elif case == "branch":
        delivery["branch"] = "--detach"
    elif case == "existing_branch":
        git(delivery["repo"], "branch", delivery["branch"])
    elif case == "existing_worktree":
        delivery["worktree"].mkdir()
        (delivery["worktree"] / "keep").write_text("private")
    elif case == "nested_worktree":
        delivery["worktree"] = delivery["repo"] / "nested"
    elif case == "symlink_parent":
        (tmp_path / "link").symlink_to(tmp_path, target_is_directory=True)
        delivery["worktree"] = tmp_path / "link/unsafe"
    elif case == "receipt_exists":
        delivery["receipt"].write_text("keep")
    elif case == "empty_tests":
        delivery["tests"] = []
    elif case == "empty_executable":
        delivery["tests"] = ['""']
    else:
        delivery["tests"] = ["   "]
    with pytest.raises(ValueError):
        apply(delivery)
    assert (
        git(delivery["repo"], "rev-parse", "--verify", "review/task")
        if case == "existing_branch"
        else git(delivery["repo"], "branch", "--list", "review/task") == ""
    )
    if case == "receipt_exists":
        assert delivery["receipt"].read_text() == "keep"


@pytest.mark.parametrize(
    "code",
    [
        "raise SystemExit(7)",
        "from pathlib import Path; Path('extra').write_text('bad')",
        "from pathlib import Path; Path('edit').write_text('bad')",
        "from pathlib import Path; Path('new').unlink(); Path('new').symlink_to('/etc/passwd')",
        "from pathlib import Path; Path('.gitignore').write_text('everything')",
    ],
)
def test_failed_or_mutating_tests_keep_worktree_without_commit(delivery, code):
    delivery["tests"] = [shlex.join([sys.executable, "-c", code])]
    with pytest.raises(ValueError):
        apply(delivery)
    assert delivery["worktree"].is_dir()
    assert git(delivery["worktree"], "rev-parse", "HEAD") == git(
        delivery["repo"], "rev-parse", "HEAD"
    )
    assert not delivery["receipt"].exists()


def test_cli_delivery_parses_required_arguments():
    parser = build_parser()
    args = parser.parse_args(
        [
            "runtime",
            "apply",
            "--repo",
            "/src",
            "--bundle",
            "/bundle",
            "--approve",
            "id",
            "--branch",
            "task",
            "--worktree",
            "/wt",
            "--test",
            "python -m pytest",
            "--receipt",
            "/receipt",
            "--json",
        ]
    )
    assert args.runtime_command == "apply" and args.test == ["python -m pytest"] and args.json
    assert (
        parser.parse_args(
            [
                "runtime",
                "project-export",
                "--repo",
                "/src",
                "--revision",
                "main",
                "--catalog",
                "/catalog",
            ]
        ).revision
        == "main"
    )
    assert (
        parser.parse_args(["runtime", "changes", "--session", "s", "--output", "/bundle"]).output
        == "/bundle"
    )
    assert (
        parser.parse_args(
            ["runtime", "publish", "--receipt", "/receipt", "--title", "Title", "--base", "main"]
        ).base
        == "main"
    )


@pytest.fixture
def published(delivery, tmp_path, monkeypatch):
    result = apply(delivery)
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", str(remote))
    git(delivery["repo"], "remote", "add", "origin", str(remote))
    git(delivery["repo"], "push", "origin", "main")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_GH_ROOT'])
with (root / 'calls').open('a') as f:
    f.write(json.dumps(args) + '\\n')
if args[:2] == ['repo', 'view']:
    print(json.dumps({'url': 'https://github.com/example/project'}))
elif args[:2] == ['pr', 'list']:
    print((root / 'existing').read_text() if (root / 'existing').exists() else '[]')
elif args[:2] == ['pr', 'create']:
    (root / 'body').write_bytes(pathlib.Path(args[args.index('--body-file') + 1]).read_bytes())
    if (root / 'fail').exists():
        print('SECRET_AUTH', file=sys.stderr)
        sys.exit(1)
    print('https://github.com/example/project/pull/17')
else:
    sys.exit(2)
"""
    )
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAKE_GH_ROOT", str(tmp_path))
    return delivery, result, remote


def publish(options, title="Reviewed change", base="main"):
    from kairos_cli.runtime_delivery import publish_review

    return publish_review(receipt=options["receipt"], title=title, base=base)


def test_publish_pushes_exact_commit_and_creates_draft_using_body_file(published, tmp_path):
    options, result, remote = published
    response = publish(options, title="--title $(do-not-run)")
    assert response["url"] == "https://github.com/example/project/pull/17"
    assert git(remote, "rev-parse", "refs/heads/review/task") == result["commit"]
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    create = calls[-1]
    assert create[:3] == ["pr", "create", "--draft"]
    assert create[create.index("--title") + 1] == "--title $(do-not-run)"
    assert create[create.index("--head") + 1] == "review/task"
    assert create[create.index("--base") + 1] == "main"
    body = (tmp_path / "body").read_text()
    assert result["review_id"] in body and result["commit"] in body and "edit" in body
    assert sys.executable in body and "returncode" in body
    assert not Path(create[create.index("--body-file") + 1]).exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "tests",
        "review_id",
        "branch",
        "tree",
        "commit",
        "schema",
        "extra",
        "head",
        "dirty",
        "wrong_base",
    ],
)
def test_publish_rejects_altered_receipt_or_worktree_before_push(published, mutation, tmp_path):
    options, result, remote = published
    changed = copy.deepcopy(result)
    if mutation == "tests":
        changed["tests"][0]["argv"] = ["false"]
    elif mutation == "review_id":
        changed["review_id"] = "0" * 64
    elif mutation in {"tree", "commit"}:
        changed[mutation] = "0" * 40
    elif mutation == "branch":
        changed["branch"] = "--all"
    elif mutation == "schema":
        changed["schema_version"] = True
    elif mutation == "extra":
        changed["extra"] = 1
    elif mutation == "head":
        git(options["worktree"], "commit", "--allow-empty", "-m", "drift")
    elif mutation == "dirty":
        (options["worktree"] / "extra").write_text("extra")
    options["receipt"].write_bytes(canonical_json(changed))
    with pytest.raises(ValueError):
        publish(options, base="--all" if mutation == "wrong_base" else "main")
    assert git(remote, "branch", "--list", "review/task") == ""
    assert not (tmp_path / "calls").exists()


def test_publish_safe_retry_after_push_and_matching_existing_draft(published, tmp_path):
    options, result, remote = published
    (tmp_path / "fail").write_text("1")
    with pytest.raises(ValueError, match="gh") as error:
        publish(options)
    assert "SECRET_AUTH" not in str(error.value)
    assert git(remote, "rev-parse", "refs/heads/review/task") == result["commit"]
    (tmp_path / "existing").write_text(
        json.dumps(
            [
                dict(
                    isDraft=True,
                    headRefOid=result["commit"],
                    headRefName="review/task",
                    baseRefName="main",
                    url="https://github.com/example/project/pull/17",
                )
            ]
        )
    )
    assert publish(options)["url"] == "https://github.com/example/project/pull/17"
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    assert len([c for c in calls if c[:2] == ["pr", "create"]]) == 1


@pytest.mark.parametrize("match", [False, True])
def test_publish_rejects_existing_nondraft_or_different_commit(published, tmp_path, match):
    options, result, _remote = published
    (tmp_path / "existing").write_text(
        json.dumps(
            [
                dict(
                    isDraft=match,
                    headRefOid="0" * 40 if match else result["commit"],
                    headRefName="review/task",
                    baseRefName="main",
                    url="https://github.com/example/project/pull/17",
                )
            ]
        )
    )
    with pytest.raises(ValueError):
        publish(options)


def test_changes_saves_canonical_validated_bundle_without_overwrite(delivery, tmp_path):
    from argparse import Namespace

    from kairos_cli.runtime_delivery import run_delivery

    bundle = json.loads(delivery["bundle"].read_bytes())

    class Client:
        async def changes(self, session):
            assert session == "session-1"
            return bundle

    destination = tmp_path / "saved.json"
    args = Namespace(session="session-1", output=str(destination))
    result = asyncio.run(run_delivery(command="changes", args=args, client=Client()))
    assert result["review_id"] == bundle["review_id"]
    assert destination.read_bytes() == canonical_json(bundle)
    assert destination.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError):
        asyncio.run(run_delivery(command="changes", args=args, client=Client()))
    destination.unlink()
    bundle["diff"] = "tampered"
    with pytest.raises(ValueError):
        asyncio.run(run_delivery(command="changes", args=args, client=Client()))
    assert not destination.exists()


def test_publish_rejects_tracked_drift_hidden_by_index_flags(published):
    options, _result, remote = published
    git(options["worktree"], "update-index", "--assume-unchanged", "edit")
    (options["worktree"] / "edit").write_text("hidden drift")
    with pytest.raises(ValueError):
        publish(options)
    assert git(remote, "branch", "--list", "review/task") == ""


def test_apply_disables_commit_and_checkout_hooks(delivery):
    hooks = delivery["repo"] / ".git/hooks"
    for name in ("post-checkout", "pre-commit", "post-commit"):
        hook = hooks / name
        hook.write_text("#!/bin/sh\ntouch hook-ran\nexit 1\n")
        hook.chmod(0o755)
    result = apply(delivery)
    assert result["commit"]
    assert not (delivery["worktree"] / "hook-ran").exists()
    assert not (delivery["repo"] / "hook-ran").exists()


def test_publish_requires_one_origin_push_destination(published, tmp_path):
    options, _result, remote = published
    other = tmp_path / "other.git"
    git(tmp_path, "init", "--bare", str(other))
    git(options["repo"], "remote", "set-url", "--push", "origin", str(other))
    with pytest.raises(ValueError):
        publish(options)
    assert git(other, "branch", "--list", "review/task") == ""
    assert git(remote, "branch", "--list", "review/task") == ""


def test_publish_targets_origin_explicitly_even_with_gh_repo_environment(
    published, tmp_path, monkeypatch
):
    options, _result, _remote = published
    monkeypatch.setenv("GH_REPO", "wrong/other")
    publish(options)
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text().splitlines()]
    create = calls[-1]
    assert create[create.index("--repo") + 1] == "github.com/example/project"


def test_apply_cannot_hide_nested_worktree_by_using_repo_subdirectory(delivery):
    delivery["worktree"] = delivery["repo"] / "nested"
    delivery["repo"] = delivery["repo"] / "dir"
    with pytest.raises(ValueError):
        apply(delivery)
    assert not delivery["worktree"].exists()


def test_publish_does_not_follow_configured_annotated_tags(published):
    options, _result, remote = published
    git(options["repo"], "tag", "-a", "do-not-publish", "-m", "local tag")
    git(options["repo"], "config", "push.followTags", "true")
    publish(options)
    assert git(remote, "tag", "--list") == ""


def test_apply_rejects_worktree_inside_an_exported_version(delivery, tmp_path):
    from kairos_runtime.projects import export_project

    exported = export_project(delivery["repo"], "main", tmp_path / "catalog")
    delivery["worktree"] = Path(exported["cwd"]) / "nested"
    with pytest.raises(ValueError):
        apply(delivery)
    assert not delivery["worktree"].exists()


def test_publish_checks_other_base_pr_before_creating_another(published, tmp_path):
    options, result, _remote = published
    # The fake models gh's real base filter: filtering on main hides an existing PR to develop.
    fake = tmp_path / "bin/gh"
    script = fake.read_text()
    script = script.replace(
        "print((root / 'existing').read_text() if (root / 'existing').exists() else '[]')",
        "print('[]' if '--base' in args else (root / 'existing').read_text())",
    )
    fake.write_text(script)
    (tmp_path / "existing").write_text(
        json.dumps(
            [
                dict(
                    isDraft=True,
                    headRefOid=result["commit"],
                    headRefName="review/task",
                    baseRefName="develop",
                    url="https://github.com/example/project/pull/17",
                )
            ]
        )
    )
    with pytest.raises(ValueError):
        publish(options)
    assert not (tmp_path / "body").exists()


def test_cli_export_dispatch_and_sanitized_apply_error(delivery, tmp_path, capsys):
    from kairos_cli.main import main

    catalog = tmp_path / "cli-catalog"
    assert (
        main(
            [
                "runtime",
                "project-export",
                "--repo",
                str(delivery["repo"]),
                "--revision",
                "main",
                "--catalog",
                str(catalog),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert Path(result["cwd"]).is_dir()
    assert (Path(result["cwd"]) / "edit").read_bytes() == b"before\n"
    assert (
        main(
            [
                "runtime",
                "apply",
                "--repo",
                str(delivery["repo"]),
                "--bundle",
                str(delivery["bundle"]),
                "--approve",
                "WRONG",
                "--branch",
                delivery["branch"],
                "--worktree",
                str(delivery["worktree"]),
                "--test",
                "true",
                "--receipt",
                str(delivery["receipt"]),
                "--json",
            ]
        )
        == 1
    )
    error = json.loads(capsys.readouterr().out)
    assert error["error"] == "delivery_failed"
    assert "approval" in error["message"]
    assert not delivery["worktree"].exists()
