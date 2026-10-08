"""Catálogo real: índice separado dos corpos e snapshots verificáveis."""

import dataclasses
import hashlib
import json

import pytest
from test_skill_runtime import skill_text, write_skill


def test_indice_ordenado_sem_corpos_e_snapshot_verificavel(tmp_path):
    from kairos_skills.catalog import catalog_asset, decode_skill_catalog
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path, "segunda", skill_text("segunda", "corpo-privado-ficticio"))
    write_skill(tmp_path, "primeira")
    write_skill(tmp_path, "invalida", "sem YAML")
    refs = tmp_path / "skills/primeira/references"
    refs.mkdir()
    (refs / "guia.md").write_text("referência fictícia", encoding="utf-8")
    (refs / "script.py").write_text("não inventariar", encoding="utf-8")
    snapshot = capture_skill_catalog(tmp_path)
    assert [e.name for e in snapshot.entries] == ["primeira", "segunda"]
    assert snapshot.omitted_skills == 1
    assert json.loads(snapshot.index_json) == [
        {"name": "primeira", "description": "Revise documentos."},
        {"name": "segunda", "description": "Revise documentos."},
    ]
    assert "corpo-privado-ficticio" not in snapshot.system_text
    assert "referência fictícia" not in snapshot.manifest_json
    assert "script.py" not in snapshot.manifest_json
    asset = catalog_asset(snapshot, "primeira", "references/guia.md")
    assert asset.sha256 == hashlib.sha256("referência fictícia".encode()).hexdigest()
    loaded = decode_skill_catalog(
        snapshot.manifest_json, snapshot.system_text, snapshot.digest, home_id=snapshot.home_id
    )
    assert loaded == snapshot
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.home_id = "outro"


@pytest.mark.parametrize(
    "bad",
    [
        "sem YAML",
        "---\nname: outra\ndescription: Revise.\n---\ncorpo",
        "---\nname: invalida\ndescription: 12\n---\ncorpo",
        "---\nname: invalida\ndescription: Revise.\n---\n",
    ],
)
def test_entrada_invalida_omitida_com_contagem(tmp_path, bad):
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path, "valida")
    write_skill(tmp_path, "invalida", bad)
    result = capture_skill_catalog(tmp_path)
    assert [entry.name for entry in result.entries] == ["valida"]
    assert result.omitted_skills == 1


def test_ativo_excesso_recusa_sem_subconjunto(tmp_path):
    from kairos_skills.catalog import SkillCatalogError
    from kairos_skills.catalog_io import capture_skill_catalog

    for i in range(256):
        write_skill(tmp_path, f"skill-{i}")
    assert len(capture_skill_catalog(tmp_path).entries) == 256
    write_skill(tmp_path, "excesso")
    with pytest.raises(SkillCatalogError, match="limite"):
        capture_skill_catalog(tmp_path)


def test_limites_indice_manifesto_e_assets_recusam_catalogo_inteiro(tmp_path, monkeypatch):
    from kairos_skills import catalog
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path)
    original = capture_skill_catalog(tmp_path)
    for key, size in [
        ("MAX_INDEX_BYTES", len(original.index_json.encode())),
        ("MAX_MANIFEST_BYTES", len(original.manifest_json.encode())),
        ("MAX_TOTAL_ASSET_BYTES", sum(a.size_bytes for e in original.entries for a in e.assets)),
    ]:
        with monkeypatch.context() as patch:
            patch.setattr(catalog, key, size)
            assert capture_skill_catalog(tmp_path).digest == original.digest
            patch.setattr(catalog, key, size - 1)
            with pytest.raises(catalog.SkillCatalogError, match="limite"):
                capture_skill_catalog(tmp_path)


def test_snapshot_adulterado_recusa_sem_reindexar(tmp_path):
    from kairos_skills.catalog import SkillCatalogError, decode_skill_catalog
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path)
    s = capture_skill_catalog(tmp_path)
    for manifest, system, digest, home in [
        (s.manifest_json, s.system_text + "novo", s.digest, s.home_id),
        (s.manifest_json, s.system_text, "0" * 64, s.home_id),
        (s.manifest_json, s.system_text, s.digest, "1" * 64),
        (s.manifest_json[:-1] + ',"format_version":1}', s.system_text, s.digest, s.home_id),
    ]:
        with pytest.raises(SkillCatalogError):
            decode_skill_catalog(manifest, system, digest, home_id=home)


@pytest.mark.parametrize(
    "reference",
    [
        "../fora.md",
        "/fora.md",
        "references//guia.md",
        "references/./guia.md",
        "references/../guia.md",
        "references\\guia.md",
        "",
        "references/ausente.md",
    ],
)
def test_referencia_hostil_ou_ausente_recusada(tmp_path, reference):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path)
    snapshot = capture_skill_catalog(tmp_path)
    with pytest.raises(SkillCatalogError):
        catalog_asset(snapshot, "revisar-docs", reference)


def test_paginas_reconstroem_unicode_sem_perda(tmp_path):
    from kairos_skills.catalog import catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
    from kairos_skills.catalog_view import encode_skill_view_page, skill_view_page

    path = write_skill(tmp_path, text=skill_text(body="á🙂\n\x01" * 2000))
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    text = read_catalog_asset(tmp_path, asset)
    pages = []
    offset = 0
    while True:
        page = skill_view_page(asset, text, offset=offset, limit=17)
        assert len(encode_skill_view_page(page).encode()) <= 32 * 1024
        assert json.loads(encode_skill_view_page(page))["text"] == page.text
        pages.append(page.text)
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert "".join(pages) == path.read_text(encoding="utf-8")
    end = skill_view_page(asset, text, offset=len(text))
    assert end.text == ""
    assert end.next_offset is None


@pytest.mark.parametrize(
    "offset,limit", [(True, 1), (-1, 1), (0, False), (0, 0), (0, 4001), (1.2, 1), (10000, 1)]
)
def test_offsets_e_tipos_hostis_recusados(tmp_path, offset, limit):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
    from kairos_skills.catalog_view import skill_view_page

    write_skill(tmp_path)
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    with pytest.raises(SkillCatalogError):
        skill_view_page(asset, read_catalog_asset(tmp_path, asset), offset=offset, limit=limit)


def test_saida_json_respeita_orcamento_com_escapes(tmp_path):
    from kairos_skills.catalog import catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset
    from kairos_skills.catalog_view import encode_skill_view_page, skill_view_page

    write_skill(tmp_path, text=skill_text(body="\x01" * 4100))
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    text = read_catalog_asset(tmp_path, asset)
    page = skill_view_page(asset, text, offset=text.index("\x01"), limit=4000)
    assert page.text == "\x01" * 4000
    assert len(encode_skill_view_page(page).encode()) < 32 * 1024
