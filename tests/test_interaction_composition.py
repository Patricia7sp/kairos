import asyncio
import sqlite3

import pytest

from kairos_integration import build_interaction_service, composition
from kairos_integration.interaction_contract import InteractionEnvelope
from kairos_providers import ModelCatalog, ProviderModelRef
from kairos_providers.gateway import ProviderGateway


def test_composition_usa_gateway_e_state_compartilhados(tmp_path):
    """Trocar o home ou não compartilhar o gateway quebraria o runtime composto."""
    service = build_interaction_service(tmp_path)

    assert isinstance(service.gateway, ProviderGateway)
    assert service.home == tmp_path
    asyncio.run(service.aclose())


def test_composition_le_config_e_state_somente_do_home_fornecido(tmp_path, monkeypatch):
    """Consultar KAIROS_HOME global misturaria perfil, catálogo e transcript."""
    supplied_home = tmp_path / "supplied"
    environment_home = tmp_path / "environment"
    supplied_home.mkdir()
    environment_home.mkdir()
    (supplied_home / "config.yaml").write_text(
        "provider: gemini\nmodel: gemini-2.0-flash\n",
        encoding="utf-8",
    )
    (environment_home / "config.yaml").write_text(
        "provider: openai\nmodel: gpt-4o\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KAIROS_HOME", str(environment_home))

    service = build_interaction_service(supplied_home)
    snapshot = service._resolve_snapshot(
        InteractionEnvelope(conversation_id="s1", source="test", content="olá")
    )

    assert snapshot.ref == ProviderModelRef("gemini", "gemini-2.0-flash")
    assert (supplied_home / "state.db").exists()
    assert not (environment_home / "state.db").exists()
    asyncio.run(service.aclose())


def test_composition_fecha_somente_os_recursos_que_criou(tmp_path, monkeypatch):
    """Omitir gateway ou banco do fechamento vazaria recursos entre superfícies."""

    class ClosingGateway:
        def __init__(self):
            self.catalog = ModelCatalog()
            self.closed = False

        async def aclose(self):
            self.closed = True

    gateway = ClosingGateway()
    monkeypatch.setattr(composition, "build_provider_gateway", lambda _home: gateway)
    service = build_interaction_service(tmp_path)

    async def close_twice():
        async with service as entered:
            assert entered is service
        await service.aclose()

    asyncio.run(close_twice())

    assert gateway.closed
    envelope = InteractionEnvelope(
        conversation_id="s1",
        source="test",
        content="olá",
    )
    with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
        asyncio.run(anext(service.stream(envelope)))
