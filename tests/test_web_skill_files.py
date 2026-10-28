"""Escrita de skills pela API preserva a raiz autorizada e arquivos externos."""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app

    return TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})


def test_save_creates_nested_skill_and_updates_the_same_file(client, tmp_path):
    arguments = {"name": "category/Legacy_Skill", "content": "initial"}
    assert client.put("/api/skills/content", json=arguments).status_code == 200
    path = tmp_path / "skills/category/Legacy_Skill/SKILL.md"
    before = path.stat()
    arguments["content"] = "updated"
    response = client.put("/api/skills/content", json=arguments)
    assert response.status_code == 200
    assert response.json()["saved"] is True
    assert path.read_text() == "updated"
    assert (path.stat().st_dev, path.stat().st_ino) == (before.st_dev, before.st_ino)


@pytest.mark.parametrize("kind", ["relative", "absolute", "empty", "dot", "nul", "surrogate"])
def test_save_rejects_invalid_names_without_writing(client, tmp_path, kind):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "SKILL.md"
    target.write_text("preserved")
    name = {
        "relative": "../outside",
        "absolute": str(outside),
        "empty": "",
        "dot": ".",
        "nul": "bad\0name",
        "surrogate": "bad\ud800name",
    }[kind]
    response = client.put(
        "/api/skills/content",
        content=json.dumps({"name": name, "content": "overwrite"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_name"
    assert target.read_text() == "preserved"
    assert not (tmp_path / "skills").exists()


@pytest.mark.parametrize("kind", ["parent_symlink", "file_symlink", "hardlink", "fifo"])
def test_save_refuses_linked_or_special_files(client, tmp_path, kind):
    skill = tmp_path / "skills/procedure"
    skill.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "SKILL.md"
    external.write_text("preserved")
    target = skill / "SKILL.md"
    reader = None
    if kind == "parent_symlink":
        skill.symlink_to(outside, target_is_directory=True)
    else:
        skill.mkdir()
        if kind == "file_symlink":
            target.symlink_to(external)
        elif kind == "hardlink":
            os.link(external, target)
        else:
            os.mkfifo(target)
            reader = os.open(target, os.O_RDONLY | os.O_NONBLOCK)
    try:
        response = client.put(
            "/api/skills/content", json={"name": "procedure", "content": "overwrite"}
        )
        assert response.status_code == 400
        assert external.read_text() == "preserved"
        if reader is not None:
            assert os.read(reader, 64) == b""
    finally:
        if reader is not None:
            os.close(reader)


def test_save_refuses_directory_replacement_during_creation(client, tmp_path, monkeypatch):
    skills = tmp_path / "skills"
    skills.mkdir()
    identity = skills.stat()
    target = skills / "procedure"
    outside = tmp_path / "outside"
    outside.mkdir()
    external = outside / "SKILL.md"
    external.write_text("preserved")
    mkdir = os.mkdir
    replaced = False

    def replace_after_mkdir(path, mode=0o777, *, dir_fd=None):
        nonlocal replaced
        mkdir(path, mode, dir_fd=dir_fd)
        requested = Path(path) == target
        if dir_fd is not None and Path(path) == Path("procedure"):
            parent = os.fstat(dir_fd)
            requested = (parent.st_dev, parent.st_ino) == (identity.st_dev, identity.st_ino)
        if requested and not replaced:
            target.rename(skills / "detached")
            target.symlink_to(outside, target_is_directory=True)
            replaced = True

    monkeypatch.setattr(os, "mkdir", replace_after_mkdir)
    response = client.put("/api/skills/content", json={"name": "procedure", "content": "overwrite"})
    assert replaced
    assert response.status_code in {400, 409}
    assert external.read_text() == "preserved"


def test_save_invalid_utf8_preserves_existing_content(client, tmp_path):
    target = tmp_path / "skills/procedure/SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("preserved")
    response = client.put(
        "/api/skills/content",
        content=json.dumps({"name": "procedure", "content": "\ud800"}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert target.read_text() == "preserved"
