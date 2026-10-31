"""Configuração presente inválida não inicia serviços com defaults ou bloqueios."""

import asyncio
import os
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from uuid import uuid4

import pytest

from kairos_integration.composition import _load_config as interaction_config
from kairos_integration.composition import build_interaction_service
from kairos_providers.settings import load_config_document
from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.host import _load_config as runtime_config

LOADERS = [interaction_config, load_config_document, runtime_config]


def invalid_entry(home, kind):
    path = home / "config.yaml"
    if kind == "directory":
        path.mkdir()
    elif kind == "dangling":
        path.symlink_to(home / "ausente.yaml")
    elif kind == "cycle":
        path.symlink_to(path)
    else:
        os.mkfifo(path)


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("kind", ["directory", "dangling", "cycle"])
def test_loaders_recusam_entrada_presente_invalida(tmp_path, loader, kind):
    invalid_entry(tmp_path, kind)
    with pytest.raises((ValueError, RuntimeErrorInfo)):
        loader(tmp_path)


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("kind", ["yaml", "utf8", "sequence"])
def test_loaders_recusam_conteudo_invalido_sem_expor_config(tmp_path, loader, kind):
    marker = uuid4().hex
    path = tmp_path / "config.yaml"
    if kind == "yaml":
        path.write_text(f'credential: ["{marker}"\n')
    elif kind == "utf8":
        path.write_bytes(f"credential: {marker}".encode() + b"\xff")
    else:
        path.write_text(f"- {marker}\n")
    with pytest.raises((ValueError, RuntimeErrorInfo)) as captured:
        loader(tmp_path)
    assert marker not in str(captured.value)
    assert marker not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("loader", LOADERS)
@pytest.mark.parametrize("content", [None, "", "null\n"])
def test_loaders_preservam_ausencia_e_documento_vazio(tmp_path, loader, content):
    if content is not None:
        (tmp_path / "config.yaml").write_text(content)
    result = loader(tmp_path)
    if loader is runtime_config:
        assert result.enabled is False
    else:
        assert result == {}


@pytest.mark.parametrize("loader", LOADERS)
def test_loaders_preservam_alias_regular(tmp_path, loader):
    source = tmp_path / "selecionada.yaml"
    source.write_text("agent_runtime:\n  model: selecionado-pelo-operador\n")
    (tmp_path / "config.yaml").symlink_to(source)
    result = loader(tmp_path)
    if loader is runtime_config:
        assert result.model == "selecionado-pelo-operador"
    else:
        assert result["agent_runtime"]["model"] == "selecionado-pelo-operador"


@pytest.mark.parametrize("kind", ["directory", "dangling", "cycle"])
def test_composicao_real_recusa_config_invalida(tmp_path, kind):
    invalid_entry(tmp_path, kind)
    service = None
    try:
        with pytest.raises(ValueError, match=r"config.yaml"):
            service = build_interaction_service(tmp_path)
    finally:
        if service is not None:
            asyncio.run(service.aclose())


def test_composicao_real_preserva_alias_com_sandbox_habilitada(tmp_path):
    source = tmp_path / "selecionada.yaml"
    source.write_text("chat:\n  sandboxed_bash: true\ncode_execution:\n  timeout_seconds: 17\n")
    (tmp_path / "config.yaml").symlink_to(source)
    service = build_interaction_service(tmp_path)
    try:
        assert service._chat_sandbox is not None
        assert service._chat_sandbox._limits.timeout_seconds == 17
    finally:
        asyncio.run(service.aclose())


@pytest.mark.parametrize("mode", ["interaction", "providers", "runtime", "service"])
def test_inicializacao_recusa_fifo_sem_bloquear(tmp_path, mode):
    invalid_entry(tmp_path, "fifo")
    program = """import asyncio, sys
from pathlib import Path
from kairos_integration.composition import _load_config, build_interaction_service
from kairos_providers.settings import load_config_document
from kairos_runtime.host import _load_config as runtime_config
from kairos_runtime.errors import RuntimeErrorInfo
loader = {'interaction': _load_config, 'providers': load_config_document,
          'runtime': runtime_config, 'service': build_interaction_service}[sys.argv[2]]
try:
    result = loader(Path(sys.argv[1]))
except (ValueError, RuntimeErrorInfo):
    print('recusado')
else:
    if sys.argv[2] == 'service':
        asyncio.run(result.aclose())
    raise SystemExit(3)
"""
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), mode],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "recusado"


@pytest.mark.parametrize("kind", ["dangling", "fifo"])
def test_host_real_publica_indisponibilidade_em_config_invalida(kind):
    program = """import asyncio, sys
from pathlib import Path
from kairos_runtime.host import serve_runtime
from kairos_runtime.client import RuntimeClient
async def run():
    home = Path(sys.argv[1])
    task = asyncio.create_task(serve_runtime(home))
    path = home / 'run/runtime.sock'
    client = RuntimeClient(path)
    try:
        for _ in range(200):
            if path.exists():
                break
            if task.done():
                await task
            await asyncio.sleep(.01)
        status = await client.status()
        assert status['state'] == 'unavailable', status
    finally:
        await client.aclose()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
asyncio.run(run())
"""
    with tempfile.TemporaryDirectory(prefix="kpr126-") as raw:
        home = Path(raw)
        invalid_entry(home, kind)
        result = subprocess.run(
            [sys.executable, "-c", program, str(home)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    assert result.returncode == 0, result.stderr
