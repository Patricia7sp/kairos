import json
import subprocess
from pathlib import Path

import pytest

from kairos_runtime.projects import discover_projects, export_project, project_metadata


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args]).decode().strip()


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Test")
    (path / "file").write_text("first\n")
    (path / ".superpowers").mkdir()
    (path / ".superpowers" / "report.md").write_text("ordinary tracked report")
    git(path, "add", ".")
    git(path, "commit", "-qm", "first")
    return path


def test_export_two_immutable_revisions_and_idempotence(repo, tmp_path):
    catalog = tmp_path / "catalog"
    first = git(repo, "rev-parse", "HEAD")
    initial = export_project(repo, "HEAD", catalog)
    workspace = catalog / first / "workspace"
    assert initial["revision"] == first
    assert initial["name"] == "project"
    assert initial["schema_version"] == 1
    assert initial["cwd"] == str(workspace)
    assert (workspace / ".superpowers" / "report.md").is_file()
    assert not (workspace / ".git").exists()
    assert (workspace / "file").stat().st_mode & 0o777 == 0o644
    manifest = catalog / first / "project.json"
    before = manifest.stat().st_mtime_ns
    assert export_project(repo, first, catalog) == initial
    assert manifest.stat().st_mtime_ns == before
    (repo / "file").write_text("second\n")
    (repo / "file").chmod(0o755)
    git(repo, "commit", "-qam", "second")
    second = export_project(repo, "HEAD", catalog)
    assert second["revision"] != first
    assert (workspace / "file").read_text() == "first\n"
    assert Path(second["cwd"]).joinpath("file").stat().st_mode & 0o777 == 0o755
    assert discover_projects((str(catalog),)) == sorted(
        [initial, second], key=lambda p: p["revision"]
    )
    assert project_metadata(workspace) == initial
    assert project_metadata(repo) is None


@pytest.mark.parametrize(
    "tamper", ["content", "manifest", "workspace_link", "file_link", "name", "excluded"]
)
def test_catalog_rejects_tampered_versions_without_repair(repo, tmp_path, tamper):
    catalog = tmp_path / "catalog"
    version = export_project(repo, "HEAD", catalog)
    workspace = Path(version["cwd"])
    manifest = workspace.parent / "project.json"
    if tamper == "content":
        (workspace / "file").write_text("tampered")
    elif tamper == "manifest":
        metadata = json.loads(manifest.read_text())
        metadata["baseline_fingerprint"] = "0" * 64
        manifest.write_text(json.dumps(metadata))
    elif tamper == "workspace_link":
        workspace.rename(workspace.with_name("moved"))
        workspace.symlink_to(workspace.with_name("moved"), target_is_directory=True)
    elif tamper == "file_link":
        (workspace / "file").unlink()
        (workspace / "file").symlink_to(repo / "file")
    elif tamper == "name":
        workspace.parent.rename(catalog / ("a" * 40))
    elif tamper == "excluded":
        (workspace / ".env").write_text("unexpected")
    with pytest.raises(ValueError):
        discover_projects((str(catalog),))
    if tamper != "name":
        with pytest.raises(ValueError):
            export_project(repo, "HEAD", catalog)


@pytest.mark.parametrize("kind", ["symlink", "submodule", "env", "cache"])
def test_export_rejects_tracked_unsafe_or_snapshot_excluded_files(repo, tmp_path, kind):
    if kind == "symlink":
        (repo / "link").symlink_to("file")
        git(repo, "add", "link")
    elif kind == "submodule":
        commit = git(repo, "rev-parse", "HEAD")
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit},sub")
    else:
        path = repo / (".env.secret" if kind == "env" else "node_modules/pkg")
        path.parent.mkdir(exist_ok=True)
        path.write_text("excluded")
        git(repo, "add", "-f", str(path))
    git(repo, "commit", "-qm", "unsafe")
    with pytest.raises(ValueError):
        export_project(repo, "HEAD", tmp_path / "catalog")
    assert not list((tmp_path / "catalog").glob("*/project.json"))


def test_export_rejects_option_revision_and_symlink_catalog_parent(repo, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        export_project(repo, "HEAD", link / "catalog")
    assert not (real / "catalog").exists()
    with pytest.raises(ValueError):
        export_project(repo, "--output=unsafe", tmp_path / "catalog")


def test_metadata_rejects_manifest_link_and_nested_workspace(repo, tmp_path):
    result = export_project(repo, "HEAD", tmp_path / "catalog")
    workspace = Path(result["cwd"])
    assert project_metadata(workspace / ".superpowers") is None
    manifest = workspace.parent / "project.json"
    saved = tmp_path / "manifest.json"
    manifest.rename(saved)
    manifest.symlink_to(saved)
    with pytest.raises(ValueError):
        project_metadata(workspace)


def test_export_normalizes_version_directory_access(repo, tmp_path):
    version = export_project(repo, "HEAD", tmp_path / "catalog")
    assert Path(version["cwd"]).parent.stat().st_mode & 0o777 == 0o755


def test_manifest_rejects_duplicate_keys(repo, tmp_path):
    version = export_project(repo, "HEAD", tmp_path / "catalog")
    manifest = Path(version["cwd"]).parent / "project.json"
    manifest.write_text(
        manifest.read_text().replace('"schema_version":1', '"schema_version":2,"schema_version":1')
    )
    with pytest.raises(ValueError):
        project_metadata(Path(version["cwd"]))


def test_export_modes_are_normalized_under_private_umask(repo, tmp_path):
    import os

    old = os.umask(0o077)
    try:
        version = export_project(repo, "HEAD", tmp_path / "catalog")
    finally:
        os.umask(old)
    workspace = Path(version["cwd"])
    assert workspace.stat().st_mode & 0o777 == 0o755
    assert (workspace / ".superpowers").stat().st_mode & 0o777 == 0o755
    assert (workspace.parent / "project.json").stat().st_mode & 0o777 == 0o644


def test_concurrent_exports_publish_one_complete_version(repo, tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    catalog = tmp_path / "catalog"
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: export_project(repo, "HEAD", catalog), range(4)))
    assert all(result == results[0] for result in results)
    assert discover_projects((str(catalog),)) == [results[0]]
    assert len(list(catalog.iterdir())) == 1


@pytest.mark.parametrize("limit", ["MAX_CONTENT_BYTES", "MAX_ENTRIES"])
def test_git_export_enforces_content_and_entry_limits(repo, tmp_path, monkeypatch, limit):
    from kairos_runtime import projects

    monkeypatch.setattr(projects, limit, 1)
    with pytest.raises(ValueError, match="limit"):
        export_project(repo, "HEAD", tmp_path / "catalog")
