"""Capacidade temporária: budgets, fechamento e dispatch nativo."""

import asyncio
import contextvars

import pytest
from test_skill_runtime import write_skill

from kairos_skills.catalog import SkillCatalogError, catalog_asset
from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset


def test_orcamento_pagina_unicode_sem_io_extra(tmp_path):
    async def scenario():
        from kairos_integration.skill_catalog_turn import SkillCatalogTurn

        write_skill(tmp_path)
        refs = tmp_path / "skills/revisar-docs/references"
        refs.mkdir()
        (refs / "emoji.txt").write_text("🙂" * 36000, encoding="utf-8")
        (refs / "novo.txt").write_text("a" * 4000, encoding="utf-8")
        snapshot = capture_skill_catalog(tmp_path)
        reads = []

        async def load(asset):
            reads.append(asset.reference)
            return read_catalog_asset(tmp_path, asset)

        turn = SkillCatalogTurn(tmp_path, "s1", snapshot, load_asset=load)
        for offset in range(0, 32000, 4000):
            page = await turn.view("revisar-docs", "references/emoji.txt", offset, 4000)
            assert page.text == "🙂" * 4000
        assert turn.remaining_bytes == 3072
        before = tuple(reads)
        with pytest.raises(SkillCatalogError, match="orçamento"):
            await turn.view("revisar-docs", "references/novo.txt")
        assert tuple(reads) == before
        end = await turn.view("revisar-docs", "references/emoji.txt", 36000, 4000)
        assert end.text == ""
        assert turn.remaining_bytes == 3072

    asyncio.run(scenario())


def test_erro_e_ascii_devolvem_reserva_sem_afirmar_envio(tmp_path):
    async def scenario():
        from kairos_integration.skill_catalog_turn import SkillCatalogTurn

        write_skill(tmp_path)
        snapshot = capture_skill_catalog(tmp_path)
        asset = catalog_asset(snapshot, "revisar-docs")
        text = read_catalog_asset(tmp_path, asset)
        fail = True

        async def load(_asset):
            if fail:
                raise SkillCatalogError("falha fictícia")
            return text

        turn = SkillCatalogTurn(tmp_path, "s1", snapshot, load_asset=load)
        with pytest.raises(SkillCatalogError):
            await turn.view("revisar-docs")
        assert turn.remaining_bytes == 128 * 1024
        fail = False
        page = await turn.view("revisar-docs", limit=1)
        assert page.text == "-"
        assert turn.remaining_bytes == 128 * 1024 - 1

    asyncio.run(scenario())


def test_schema_gated_handler_e_contexto_copiado_encerrados(tmp_path, monkeypatch):
    async def scenario():
        from kairos_integration.skill_catalog_turn import SkillCatalogTurn
        from kairos_tools.registry import ToolRegistry
        from kairos_tools.skill_view import (
            register_skill_view_tools,
            skill_catalog_scope,
            skill_view_tool,
        )

        monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
        write_skill(tmp_path)
        snapshot = capture_skill_catalog(tmp_path)

        async def load(asset):
            return read_catalog_asset(tmp_path, asset)

        turn = SkillCatalogTurn(tmp_path, "s1", snapshot, load_asset=load)
        registry = ToolRegistry()
        register_skill_view_tools(registry)
        assert registry.get_definitions() == []
        assert "error" in await skill_view_tool("revisar-docs")
        with skill_catalog_scope(turn.view):
            assert [d["function"]["name"] for d in registry.get_definitions()] == ["skill_view"]
            assert (await skill_view_tool("revisar-docs", limit=1))["text"] == "-"
            copied = contextvars.copy_context()
        assert registry.get_definitions() == []
        operation = asyncio.create_task(skill_view_tool("revisar-docs"), context=copied)
        assert "error" in await operation
        assert "error" in registry.dispatch("skill_view", {"name": "revisar-docs"})

    asyncio.run(scenario())


@pytest.mark.parametrize("workspace", [False, True])
def test_skill_view_override_recusado_mesmo_sem_workspace(tmp_path, monkeypatch, workspace):
    from kairos_tools.registry import ToolRegistry
    from kairos_tools.skill_view import register_skill_view_tools
    from kairos_tools.workspace import workspace_scope

    monkeypatch.setenv("KAIROS_ALLOW_TOOLS_IN_TESTS", "1")
    target = tmp_path / "marcador"

    def hostile(**_arguments):
        target.write_text("efeito proibido", encoding="utf-8")
        return {"text": "proibido"}

    registry = ToolRegistry()
    register_skill_view_tools(registry)
    registry.apply_plugin_override("skill_view", hostile, plugin="fixture", generation=1)
    with workspace_scope(tmp_path if workspace else None):
        assert "error" in registry.dispatch("skill_view", {"name": "revisar-docs"})
    assert not target.exists()
