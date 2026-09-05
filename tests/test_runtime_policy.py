from __future__ import annotations

from pathlib import Path

import pytest

from kairos_runtime.contracts import RuntimeCapabilities
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.policy import (
    RUNTIME_V1_FEATURES,
    authorize_directory,
    capture_directory_identity,
    negotiate,
    revalidate_directory_identity,
    validate_sandbox,
)


def test_alias_tem_mesma_identidade(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    assert authorize_directory(str(alias), (str(project),)) == str(project.resolve())


@pytest.mark.parametrize("candidate_kind", ["external", "subdirectory", "file", "missing"])
def test_authorize_directory_rejects_everything_except_registered_root(
    tmp_path: Path, candidate_kind: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    candidate = tmp_path / "outside"
    if candidate_kind == "subdirectory":
        candidate = project / "nested"
        candidate.mkdir()
    elif candidate_kind == "file":
        candidate.write_text("not a directory", encoding="utf-8")
    elif candidate_kind == "external":
        candidate.mkdir()

    with pytest.raises(RuntimeErrorInfo) as exc_info:
        authorize_directory(str(candidate), (str(project),))

    assert exc_info.value.code == "invalid_directory"
    assert exc_info.value.retryable is False


def test_directory_identity_detects_path_replacement(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    identity = capture_directory_identity(str(project), (str(project),))
    moved = tmp_path / "old-project"
    project.rename(moved)
    project.mkdir()

    with pytest.raises(RuntimeErrorInfo) as exc_info:
        revalidate_directory_identity(identity)

    assert exc_info.value.code == "invalid_directory"


def test_directory_identity_detects_symlink_retarget(tmp_path: Path) -> None:
    project = tmp_path / "project"
    other = tmp_path / "other"
    project.mkdir()
    other.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(project, target_is_directory=True)
    identity = capture_directory_identity(str(alias), (str(project),))
    alias.unlink()
    alias.symlink_to(other, target_is_directory=True)

    with pytest.raises(RuntimeErrorInfo) as exc_info:
        revalidate_directory_identity(identity)

    assert exc_info.value.code == "invalid_directory"


def test_directory_identity_preserves_symlink_before_parent_segment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "child").mkdir()
    (second / "child").mkdir()
    first_project = first / "project"
    second_project = second / "project"
    lexical_project = tmp_path / "project"
    first_project.mkdir()
    second_project.mkdir()
    lexical_project.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(first / "child", target_is_directory=True)
    monkeypatch.chdir(tmp_path)
    raw = str(Path("alias") / ".." / "project")
    identity = capture_directory_identity(raw, (str(first_project), str(lexical_project)))

    assert Path(identity.requested_path).is_absolute()
    assert ".." in Path(identity.requested_path).parts
    assert identity.canonical_path == str(first_project.resolve())
    alias.unlink()
    alias.symlink_to(second / "child", target_is_directory=True)

    with pytest.raises(RuntimeErrorInfo) as exc_info:
        revalidate_directory_identity(identity)

    assert exc_info.value.code == "invalid_directory"


@pytest.mark.parametrize("profile", ["read_only", "workspace_write"])
def test_restricted_sandbox_profiles_do_not_need_broad_consent(profile: str) -> None:
    assert validate_sandbox(profile, broad_enabled=False, consent=False) == profile


@pytest.mark.parametrize(
    ("broad_enabled", "consent"),
    [(False, False), (False, True), (True, False)],
)
def test_broad_access_requires_configuration_and_consent(
    broad_enabled: bool, consent: bool
) -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        validate_sandbox("broad_access", broad_enabled=broad_enabled, consent=consent)

    assert exc_info.value.code == "invalid_policy"
    assert exc_info.value.retryable is False


def test_broad_access_is_allowed_when_configured_and_consented() -> None:
    assert validate_sandbox("broad_access", broad_enabled=True, consent=True) == "broad_access"


def test_unknown_sandbox_profile_is_rejected() -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        validate_sandbox("unrestricted", broad_enabled=True, consent=True)

    assert exc_info.value.code == "invalid_policy"


def test_negotiate_accepts_v1_and_filters_unsupported_features() -> None:
    capabilities = RuntimeCapabilities(
        protocol_version=1,
        features=frozenset({"text", "resume", "delta_replay", "future_feature"}),
    )

    assert negotiate(capabilities) == RuntimeCapabilities(1, frozenset({"text", "resume"}))


def test_negotiate_rejects_protocol_other_than_v1() -> None:
    with pytest.raises(RuntimeErrorInfo) as exc_info:
        negotiate(RuntimeCapabilities(2, RUNTIME_V1_FEATURES))

    assert exc_info.value.code == "incompatible"
    assert exc_info.value.retryable is False


def test_negotiate_allows_no_optional_capabilities() -> None:
    assert negotiate(RuntimeCapabilities(1, frozenset())) == RuntimeCapabilities(1, frozenset())
