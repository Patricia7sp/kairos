"""Leitor contido: arquivos instalados reais, sem seguir links."""

import os
import subprocess
import sys

import pytest
from test_skill_runtime import write_skill


@pytest.mark.parametrize("root_kind", ["ausente", "link", "vazio"])
def test_raiz_invalida_ou_catalogo_vazio_recusa(tmp_path, root_kind):
    from kairos_skills.catalog import SkillCatalogError
    from kairos_skills.catalog_io import capture_skill_catalog

    if root_kind == "link":
        external = tmp_path / "externo"
        external.mkdir()
        (tmp_path / "skills").symlink_to(external, target_is_directory=True)
    elif root_kind == "vazio":
        (tmp_path / "skills").mkdir()
    with pytest.raises(SkillCatalogError):
        capture_skill_catalog(tmp_path)


def test_utf8_invalido_omitido(tmp_path):
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path, "valida")
    write_skill(tmp_path, "invalida").write_bytes(b"\xff")
    result = capture_skill_catalog(tmp_path)
    assert result.omitted_skills == 1


def test_referencias_invalidas_contadas_e_limites_globais_recusados(tmp_path):
    from kairos_skills.catalog import SkillCatalogError
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path)
    refs = tmp_path / "skills/revisar-docs/references"
    refs.mkdir()
    (refs / "grande.md").write_bytes(b"a" * (256 * 1024 + 1))
    (refs / "link.md").symlink_to("grande.md")
    (refs / "invalida.txt").write_bytes(b"\xff")
    assert capture_skill_catalog(tmp_path).omitted_references == 3
    for i in range(129):
        (refs / f"guia-{i}.md").write_text("ok", encoding="utf-8")
    with pytest.raises(SkillCatalogError, match="limite"):
        capture_skill_catalog(tmp_path)


@pytest.mark.parametrize("kind", ["skill-link", "arquivo-link", "hardlink", "fifo"])
def test_arquivos_nao_regulares_nao_sao_anunciados(tmp_path, kind):
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path, "valida")
    bad = write_skill(tmp_path, "invalida")
    if kind == "skill-link":
        bad.unlink()
        bad.parent.rmdir()
        bad.parent.symlink_to(tmp_path / "skills/valida", target_is_directory=True)
    else:
        bad.unlink()
        if kind == "arquivo-link":
            bad.symlink_to(tmp_path / "skills/valida/SKILL.md")
        elif kind == "hardlink":
            external = tmp_path / "externo.md"
            external.write_text("conteúdo externo fictício", encoding="utf-8")
            os.link(external, bad)
        else:
            os.mkfifo(bad)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import sys; from pathlib import Path; from kairos_skills.catalog_io import capture_skill_catalog; s=capture_skill_catalog(Path(sys.argv[1])); assert [e.name for e in s.entries]==['valida']; assert s.omitted_skills==1",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            assert result.returncode == 0, result.stderr
            return
    result = capture_skill_catalog(tmp_path)
    assert [e.name for e in result.entries] == ["valida"]
    assert result.omitted_skills == 1


@pytest.mark.parametrize("change", ["editar", "apagar", "hardlink", "link"])
def test_asset_editado_ou_apagado_nao_carrega_versao_nova(tmp_path, change):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset

    path = write_skill(tmp_path)
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    if change == "editar":
        path.write_text("versão nova fictícia", encoding="utf-8")
    else:
        path.unlink()
        if change == "link":
            external = tmp_path / "externo.md"
            external.write_text("conteúdo externo fictício", encoding="utf-8")
            path.symlink_to(external)
        elif change == "hardlink":
            external = tmp_path / "externo.md"
            external.write_text("conteúdo externo fictício", encoding="utf-8")
            os.link(external, path)
    with pytest.raises(SkillCatalogError) as error:
        read_catalog_asset(tmp_path, asset)
    assert "conteúdo externo" not in str(error.value)


@pytest.mark.parametrize(
    "component", ["home", "skills", "skill", "references", "subdir", "arquivo"]
)
def test_symlink_em_componentes_recusa_asset(tmp_path, component):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset

    home = tmp_path / "home"
    write_skill(home)
    path = home / "skills/revisar-docs/references/sub/guia.md"
    path.parent.mkdir(parents=True)
    path.write_text("conteúdo autorizado", encoding="utf-8")
    asset = catalog_asset(capture_skill_catalog(home), "revisar-docs", "references/sub/guia.md")
    paths = {
        "home": home,
        "skills": home / "skills",
        "skill": home / "skills/revisar-docs",
        "references": path.parent.parent,
        "subdir": path.parent,
        "arquivo": path,
    }
    target = paths[component]
    moved = target.with_name(target.name + "-original")
    target.rename(moved)
    target.symlink_to(moved, target_is_directory=component != "arquivo")
    with pytest.raises(SkillCatalogError):
        read_catalog_asset(home, asset)


def test_alteracao_durante_read_recusa_e_fecha_descriptores(tmp_path, monkeypatch):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset

    path = write_skill(tmp_path)
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    original_read = os.read
    observed = []

    def change(fd, count):
        observed.append(fd)
        data = original_read(fd, count)
        path.write_text("versão alterada", encoding="utf-8")
        return data

    monkeypatch.setattr(os, "read", change)
    with pytest.raises(SkillCatalogError):
        read_catalog_asset(tmp_path, asset)
    for fd in set(observed):
        with pytest.raises(OSError):
            os.fstat(fd)


def test_cancelamento_fecha_leitura(tmp_path, monkeypatch):
    import asyncio

    from kairos_skills.catalog import catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset

    write_skill(tmp_path)
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    observed = []

    def interrupt(fd, _count):
        observed.append(fd)
        raise asyncio.CancelledError

    monkeypatch.setattr(os, "read", interrupt)
    with pytest.raises(asyncio.CancelledError):
        read_catalog_asset(tmp_path, asset)
    with pytest.raises(OSError):
        os.fstat(observed[0])


def test_troca_do_path_apos_abertura_nao_le_conteudo_externo(tmp_path, monkeypatch):
    from kairos_skills.catalog import SkillCatalogError, catalog_asset
    from kairos_skills.catalog_io import capture_skill_catalog, read_catalog_asset

    path = write_skill(tmp_path)
    asset = catalog_asset(capture_skill_catalog(tmp_path), "revisar-docs")
    original_open = os.open
    original = path.read_text(encoding="utf-8")

    def replace(name, flags, **kwargs):
        fd = original_open(name, flags, **kwargs)
        if name == "SKILL.md":
            path.rename(path.with_name("original.md"))
            path.write_text("conteúdo externo", encoding="utf-8")
        return fd

    monkeypatch.setattr(os, "open", replace)
    try:
        result = read_catalog_asset(tmp_path, asset)
    except SkillCatalogError:
        result = original
    assert result == original


def test_excesso_em_subdiretorio_nao_vira_omissao(tmp_path):
    from kairos_skills.catalog import SkillCatalogError
    from kairos_skills.catalog_io import capture_skill_catalog

    write_skill(tmp_path)
    refs = tmp_path / "skills/revisar-docs/references/sub"
    refs.mkdir(parents=True)
    for i in range(129):
        (refs / f"guia-{i}.md").write_text("ok", encoding="utf-8")
    with pytest.raises(SkillCatalogError, match="limite"):
        capture_skill_catalog(tmp_path)


def test_total_excedido_interrompe_varredura(tmp_path, monkeypatch):
    from kairos_skills import catalog, catalog_io

    path = write_skill(tmp_path)
    refs = tmp_path / "skills/revisar-docs/references"
    refs.mkdir()
    for i in range(10):
        (refs / f"guia-{i}.md").write_text("a" * 100, encoding="utf-8")
    monkeypatch.setattr(catalog, "MAX_TOTAL_ASSET_BYTES", path.stat().st_size + 100)
    original_read = catalog_io._read
    reads = []

    def count(directory, filename, limit):
        reads.append(filename)
        return original_read(directory, filename, limit)

    monkeypatch.setattr(catalog_io, "_read", count)
    with pytest.raises(catalog.SkillCatalogError, match="limite"):
        catalog_io.capture_skill_catalog(tmp_path)
    assert len(reads) <= 3  # SKILL + uma referência permitida + a que excede.
