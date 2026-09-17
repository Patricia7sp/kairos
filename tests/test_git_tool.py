"""Ferramenta ``git`` — subcomandos delimitados, endurecimento e registro."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from kairos_tools.git import _git_available, git_tool, register_git_tool
from kairos_tools.registry import ToolRegistry

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git não instalado")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _run(r, "init", "-b", "main")
    _run(r, "config", "user.email", "teste@kairos")
    _run(r, "config", "user.name", "Teste Kairos")
    (r / "a.txt").write_text("um\n", encoding="utf-8")
    _run(r, "add", "a.txt")
    _run(r, "commit", "-m", "inicial")
    return r


def test_status_reflete_alteracao(repo):
    (repo / "a.txt").write_text("um\ndois\n", encoding="utf-8")
    result = asyncio.run(git_tool(subcommand="status", cwd=str(repo)))
    assert result["success"] is True
    assert "a.txt" in result["stdout"]
    assert "M" in result["stdout"]


def test_diff_mostra_conteudo(repo):
    with (repo / "a.txt").open("a", encoding="utf-8") as stream:
        stream.write("dois\n")
    result = asyncio.run(git_tool(subcommand="diff", cwd=str(repo)))
    assert result["success"] is True
    assert "+dois" in result["stdout"]


def test_log_mostra_commit(repo):
    result = asyncio.run(git_tool(subcommand="log", cwd=str(repo)))
    assert result["success"] is True
    assert "inicial" in result["stdout"]


def test_commit_cria_commit(repo):
    (repo / "b.txt").write_text("novo\n", encoding="utf-8")
    result = asyncio.run(
        git_tool(subcommand="commit", cwd=str(repo), message="adiciona b", paths=["b.txt"])
    )
    assert result["success"] is True
    assert "adiciona b" in _run(repo, "log", "-1", "--pretty=%s").stdout


def test_commit_sem_mensagem_recusa_fechado(repo):
    result = asyncio.run(git_tool(subcommand="commit", cwd=str(repo)))
    assert result["success"] is False
    assert result["exit_code"] == -1
    assert "message" in result["error"]


def test_commit_sem_mudancas_nao_finge_sucesso(repo):
    result = asyncio.run(git_tool(subcommand="commit", cwd=str(repo), message="nada"))
    assert result["success"] is False
    assert result["exit_code"] != 0


def test_subcomando_nao_suportado_recusa(repo):
    result = asyncio.run(git_tool(subcommand="push", cwd=str(repo)))
    assert result["success"] is False
    assert result["error"] == "subcomando não suportado: push"


def test_path_fora_do_repo_recusado(repo):
    for ruim in (["/etc/passwd"], ["../fuga.txt"], ["-o", "a.txt"]):
        result = asyncio.run(git_tool(subcommand="commit", cwd=str(repo), message="m", paths=ruim))
        assert result["success"] is False
        assert result["exit_code"] == -1


def test_cwd_inexistente_recusa(repo):
    result = asyncio.run(git_tool(subcommand="status", cwd=str(repo / "nao-existe")))
    assert result["success"] is False
    assert result["exit_code"] == -1


def test_diretorio_sem_repo_recusa(repo):
    result = asyncio.run(git_tool(subcommand="status", cwd=str(repo.parent)))
    assert result["success"] is False
    assert result["exit_code"] != 0


def test_registra_no_toolset_do_git():
    reg = ToolRegistry()
    register_git_tool(reg)
    assert "git" in reg.get_all_tool_names()
    assert reg.is_toolset_available("git") is _git_available()


def test_requisito_inviavel_omite_a_ferramenta():
    reg = ToolRegistry()
    register_git_tool(reg)
    reg.register_toolset("git", requirement=lambda: False)
    assert reg.is_toolset_available("git") is False
    assert all(d["function"]["name"] != "git" for d in reg.get_definitions())
