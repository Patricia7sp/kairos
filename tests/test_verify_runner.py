"""Executor do `kairos verify`: fases reais e readiness de porta."""

from __future__ import annotations

import socket
import sys

import pytest

from kairos_cli.verify_recipe import Recipe
from kairos_cli.verify_runner import (
    PhaseResult,
    ReadinessResult,
    UnsupportedCommand,
    command_argv,
    run_verify,
)


def _porta_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_fases_ok_e_shape_do_resultado(tmp_path):
    recipe = Recipe(
        "App",
        kind="node",
        bootstrap=["true"],
        build=["true"],
        test=["true"],
    )
    result = run_verify(tmp_path, recipe, skip_start=True)
    assert result.ok
    assert [p.phase for p in result.phases] == ["bootstrap", "build", "test"]
    assert all(p.exit_code == 0 for p in result.phases)
    assert all(p.duration >= 0 for p in result.phases)
    payload = result.to_dict()
    assert payload["recipe"] == "App"
    assert payload["ok"] is True
    assert [p["phase"] for p in payload["phases"]] == ["bootstrap", "build", "test"]
    assert payload["readiness"] is None


def test_fase_falha_para_e_marca_resultado(tmp_path):
    recipe = Recipe("App", kind="node", bootstrap=["true"], build=["false"])
    result = run_verify(tmp_path, recipe, skip_start=True)
    assert not result.ok
    assert [p.phase for p in result.phases] == ["bootstrap", "build"]
    # stop_on_failure encerra antes das fases seguintes
    assert result.phases[1].exit_code != 0
    assert not result.phases[1].ok


def test_timeout_de_fase_marca_timed_out_e_efetivo(tmp_path):
    recipe = Recipe("App", kind="node", test=[f"{sys.executable} -c 'import time; time.sleep(60)'"])
    result = run_verify(tmp_path, recipe, phases=("test",), phase_timeout=0.2, skip_start=True)
    assert not result.ok
    phase = result.phases[0]
    assert phase.timed_out is True
    assert phase.duration < 5  # não espera os 60s; o timeout realmente trava


def test_selecao_de_fases_por_parametro(tmp_path):
    recipe = Recipe("App", kind="node", build=["true"], test=["true"])
    result = run_verify(tmp_path, recipe, phases=("build",), skip_start=True)
    assert [p.phase for p in result.phases] == ["build"]


def test_sem_fase_selecionada_retorna_vazio(tmp_path):
    recipe = Recipe("App", kind="node", test=["true"])
    result = run_verify(tmp_path, recipe, phases=(), skip_start=True)
    assert result.ok
    assert result.phases == []


def test_readiness_serve_http(tmp_path):
    port = _porta_livre()
    recipe = Recipe(
        "Servidor",
        kind="node",
        start=f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
        port=port,
    )
    result = run_verify(tmp_path, recipe, phases=(), ready_timeout=10, port_override=port)
    assert result.readiness is not None
    assert result.readiness.ready is True
    assert result.readiness.status_code == 200
    # o grupo foi derrubado: a porta voltou a ser livre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


def test_readiness_nao_serve_timeout_e_limpa_o_grupo(tmp_path):
    port = _porta_livre()
    # processo que segura a porta aberta mas nunca responde HTTP
    sentinela = tmp_path / "server_hang.py"
    sentinela.write_text(
        "import socket, sys, time\n"
        "port = int(sys.argv[1])\n"
        "s = socket.socket()\n"
        "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "s.bind(('127.0.0.1', port))\n"
        "s.listen(1)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    recipe = Recipe(
        "Servidor",
        kind="node",
        start=f"{sys.executable} {sentinela} {port}",
        port=port,
    )
    result = run_verify(tmp_path, recipe, phases=(), ready_timeout=1.5, port_override=port)
    assert result.readiness is not None
    assert result.readiness.ready is False
    assert result.readiness.duration >= 1.0
    assert result.readiness.error is not None
    assert not result.ok
    # o grupo foi derrubado: a porta voltou a ser livre
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


def test_start_respeita_port_override(tmp_path):
    port = _porta_livre()
    outro = _porta_livre()
    recipe = Recipe(
        "Servidor",
        kind="node",
        start=f"{sys.executable} -m http.server {port} --bind 127.0.0.1",
        port=port,
    )
    result = run_verify(tmp_path, recipe, phases=(), ready_timeout=10, port_override=outro)
    # override muda a URL de readiness, então nada responde no outro pedido
    assert result.readiness is not None
    assert result.readiness.ready is False
    assert str(outro) in result.readiness.url


def test_skip_start_nao_sente_readiness(tmp_path):
    recipe = Recipe("Servidor", kind="node", start="true", port=1)
    result = run_verify(tmp_path, recipe, phases=(), skip_start=True)
    assert result.readiness is None
    assert result.ok


def test_phase_result_propriedades():
    ok = PhaseResult("build", "true", 0, 0.1, "")
    assert ok.ok is True
    falha = PhaseResult("build", "false", 1, 0.1, "")
    assert falha.ok is False
    timeout = PhaseResult("build", "sleep", None, 0.1, "", timed_out=True)
    assert timeout.ok is False
    assert timeout.to_dict()["ok"] is False


def test_readiness_result_propriedades():
    ready = ReadinessResult("http://127.0.0.1:1/", True, 200, 0.1)
    assert ready.to_dict()["ready"] is True
    assert ready.to_dict()["statusCode"] == 200


# --- executor sem shell --------------------------------------------------


def test_command_argv_resolve_aspas_e_palavras():
    assert command_argv("npm run build") == ["npm", "run", "build"]
    assert command_argv("python manage.py test") == ["python", "manage.py", "test"]
    assert command_argv("uvicorn 'main:app' --host 127.0.0.1 --port 8000") == [
        "uvicorn",
        "main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
    ]


@pytest.mark.parametrize(
    "cmd",
    [
        "a && b",
        "a ; b",
        "a | b",
        "a > f",
        "a < f",
        "echo *.py",
        "echo $HOME",
        "echo `id`",
        "echo ~/x",
        "echo a {b}",
    ],
)
def test_command_argv_recusa_metacaractere_de_shell(cmd):
    with pytest.raises(UnsupportedCommand):
        command_argv(cmd)


def test_fase_com_metacaractere_recusa_barulhento(tmp_path):
    recipe = Recipe("App", kind="node", test=["echo ok && echo mais"])
    result = run_verify(tmp_path, recipe, phases=("test",), skip_start=True)
    assert not result.ok
    fase = result.phases[0]
    assert fase.unsupported is not None
    assert fase.exit_code is None
    assert "metacaractere" in fase.unsupported
    assert fase.to_dict()["unsupported"] is not None


def test_start_com_metacaractere_recusa_barulhento(tmp_path):
    recipe = Recipe("Servidor", kind="node", start="echo oi && sleep 5", port=1)
    result = run_verify(tmp_path, recipe, phases=())
    assert not result.ok
    assert result.readiness is not None
    assert result.readiness.ready is False
    assert "metacaractere" in (result.readiness.error or "")
