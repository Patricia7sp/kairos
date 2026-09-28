"""P3 — limites `code_execution.*` e sua aplicação no executor de sandbox.

Comportamento real, não snapshot: as ambiguidades da spec (teto único no fio
do Codex) são exercitadas na aplicação (`DockerWorker.execute`), não contornadas
por calar a entrada inválida.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.parametrize(
    "raw",
    [
        None,
        {},
        {"timeout_seconds": 120, "max_stdout_bytes": 4096, "max_stderr_bytes": 512},
    ],
)
def test_parse_valida_entrada_valida(raw):
    from kairos_runtime.execution import parse_execution_limits

    limites = parse_execution_limits(raw)
    if raw is None or "timeout_seconds" not in raw:
        assert (limites.timeout_seconds, limites.max_stdout_bytes, limites.max_stderr_bytes) == (
            300,
            50_000,
            10_000,
        )
    else:
        assert (limites.timeout_seconds, limites.max_stdout_bytes, limites.max_stderr_bytes) == (
            120,
            4096,
            512,
        )


@pytest.mark.parametrize(
    "raw,pedaco",
    [
        ("300", "mapeamento"),
        ({"desconhecida": 1}, "chave desconhecida"),
        ({"timeout_seconds": "300"}, "inteiro positivo"),
        ({"timeout_seconds": 0}, "inteiro positivo"),
        ({"timeout_seconds": -5}, "inteiro positivo"),
        ({"timeout_seconds": True}, "inteiro positivo"),
        ({"timeout_seconds": 3601}, "acima do teto de 3600"),
        ({"max_stdout_bytes": 1_048_577}, "acima do teto de 1048576"),
        ({"max_stderr_bytes": 1_048_577}, "acima do teto de 1048576"),
        ({"max_stderr_bytes": 0}, "inteiro positivo"),
    ],
)
def test_parse_fail_closed_nao_cala_invalido(raw, pedaco):
    from kairos_runtime.execution import MAX_TIMEOUT_SECONDS, parse_execution_limits

    # os tetos são sanidade, não política: valores no limite passam
    assert parse_execution_limits({"timeout_seconds": MAX_TIMEOUT_SECONDS})
    with pytest.raises(ValueError, match=pedaco):
        parse_execution_limits(raw)


def test_load_sem_config_usado_no_home_padroes(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    assert (tmp_path / "config.yaml").exists() is False
    assert load_execution_limits(tmp_path) == load_execution_limits(tmp_path)


def test_load_aplica_code_execution_do_home(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    (tmp_path / "config.yaml").write_text(
        "code_execution:\n  timeout_seconds: 42\n  max_stdout_bytes: 800\n",
        encoding="utf-8",
    )
    limites = load_execution_limits(tmp_path)
    assert limites.timeout_seconds == 42
    assert limites.max_stdout_bytes == 800
    assert limites.max_stderr_bytes == 10_000


def test_load_sem_chave_code_execution_usa_padroes(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    (tmp_path / "config.yaml").write_text("verbo_conjugado: tralalí\n", encoding="utf-8")
    limites = load_execution_limits(tmp_path)
    assert (limites.timeout_seconds, limites.max_stdout_bytes, limites.max_stderr_bytes) == (
        300,
        50_000,
        10_000,
    )


def test_load_config_malformado_levanta_nomeado(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    (tmp_path / "config.yaml").write_text("code_execution: [", encoding="utf-8")
    with pytest.raises(ValueError, match=r"config.yaml ilegível"):
        load_execution_limits(tmp_path)


def test_load_code_execution_invalido_fail_closed(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    (tmp_path / "config.yaml").write_text(
        "code_execution:\n  timeout_seconds: [300]\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="timeout_seconds"):
        load_execution_limits(tmp_path)


def test_load_topo_nao_mapeamento_fail_closed(tmp_path):
    from kairos_runtime.execution import load_execution_limits

    (tmp_path / "config.yaml").write_text("- um\n- dois\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapeamento de topo"):
        load_execution_limits(tmp_path)


def test_execute_aplica_limites_configurados(tmp_path):
    from kairos_runtime.execution import ExecutionLimits
    from kairos_runtime.experimental.docker_worker import DockerWorker

    chamadas = []

    class Rpc:
        async def aclose(self):
            pass

        async def call(self, metodo, payload):
            chamadas.append((metodo, payload))
            return {"exitCode": 0, "stdout": "ok", "stderr": ""}

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker._owned = True
        worker._rpc = Rpc()
        try:
            await worker.execute(
                ["echo", "x"],
                limits=ExecutionLimits(timeout_seconds=42, max_stdout_bytes=1234),
            )
        finally:
            worker._owned = False
            await worker.aclose()

    asyncio.run(scenario())
    assert len(chamadas) == 1
    _, payload = chamadas[0]
    assert payload["timeoutMs"] == 42_000
    assert payload["outputBytesCap"] == 1234


def test_execute_limites_none_mantem_comportamento_historico(tmp_path):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    chamadas = []

    class Rpc:
        async def aclose(self):
            pass

        async def call(self, metodo, payload):
            chamadas.append((metodo, payload))
            return {"exitCode": 0, "stdout": "ok", "stderr": ""}

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker._owned = True
        worker._rpc = Rpc()
        try:
            await worker.execute(["echo", "x"], timeout_ms=2500)
        finally:
            worker._owned = False
            await worker.aclose()

    asyncio.run(scenario())
    _, payload = chamadas[0]
    assert payload["timeoutMs"] == 2500
    assert payload["outputBytesCap"] == 65536


def test_execute_timeout_configurado_fora_do_teto_fail_closed(tmp_path):
    from kairos_runtime.execution import MAX_TIMEOUT_SECONDS, ExecutionLimits
    from kairos_runtime.experimental.docker_worker import DockerWorker

    chamadas = []

    class Rpc:
        async def aclose(self):
            pass

        async def call(self, metodo, payload):
            chamadas.append((metodo, payload))
            return {"exitCode": 0}

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker._owned = True
        worker._rpc = Rpc()
        try:
            absurdos = ExecutionLimits(
                timeout_seconds=MAX_TIMEOUT_SECONDS * 10 + 1,
                max_stdout_bytes=1,
                max_stderr_bytes=1,
            )
            with pytest.raises(ValueError, match="comando ou timeout inválido"):
                await worker.execute(["true"], limits=absurdos)
        finally:
            worker._owned = False
            await worker.aclose()

    asyncio.run(scenario())
    assert chamadas == []


def test_constantes_da_spec_rastreaveis():
    from kairos_runtime.execution import (
        DEFAULT_MAX_STDERR_BYTES,
        DEFAULT_MAX_STDOUT_BYTES,
        DEFAULT_TIMEOUT_SECONDS,
        MAX_TOOL_CALLS,
    )

    assert DEFAULT_TIMEOUT_SECONDS == 300
    assert DEFAULT_MAX_STDOUT_BYTES == 50_000
    assert DEFAULT_MAX_STDERR_BYTES == 10_000
    assert MAX_TOOL_CALLS == 50
