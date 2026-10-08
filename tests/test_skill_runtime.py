"""Skills selecionadas: leitura real e snapshots contidos."""

import hashlib
import os
import subprocess
import sys
from dataclasses import FrozenInstanceError

import pytest


def skill_text(name="revisar-docs", body="Procedimento fictício."):
    return f"---\nname: {name}\ndescription: Revise documentos.\nversion: antiga\n---\n{body}\n"


def write_skill(home, name="revisar-docs", text=None):
    path = home / "skills" / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(skill_text(name) if text is None else text, encoding="utf-8")
    return path


def test_ordem_deduplicacao_hash_e_imutabilidade(tmp_path):
    from kairos_skills.runtime import load_selected_skills

    text = skill_text("segunda", 'Texto "citado" e ação.')
    write_skill(tmp_path, "primeira")
    write_skill(tmp_path, "segunda", text)
    selected = load_selected_skills(tmp_path, ["segunda", "primeira", "segunda"])
    assert [s.name for s in selected] == ["segunda", "primeira"]
    assert selected[0].text == text
    assert selected[0].sha256 == hashlib.sha256(text.encode()).hexdigest()
    assert selected[0].version == "antiga"
    with pytest.raises(FrozenInstanceError):
        selected[0].text = "novo"


def test_sem_selecao_nao_abre_catalogo(tmp_path, monkeypatch):
    from kairos_skills.runtime import load_selected_skills

    monkeypatch.setattr(os, "open", lambda *_a, **_kw: pytest.fail("abriu catálogo"))
    assert load_selected_skills(tmp_path, []) == ()


@pytest.mark.parametrize(
    "name", ["../fora", "/tmp/fora", "*", "categoria/nome", "nome\n", "", "A", "a" * 65]
)
def test_nome_hostil_recusado(tmp_path, name):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    with pytest.raises(SkillSelectionError):
        load_selected_skills(tmp_path, [name])


@pytest.mark.parametrize(
    "header",
    [
        "name: 12\ndescription: Revise.",
        "name: revisar-docs\ndescription: 12",
        "name: revisar-docs\ndescription: Revise.\nversion: 12",
        "name: revisar-docs\ndescription: Revise.\nmetadata: segredo-ficticio",
        "name: revisar-docs\ndescription: Revise.\nmetadata:\n  kairos: segredo-ficticio",
        "name: revisar-docs\ndescription: Revise.\ntags: segredo-ficticio",
        "name: revisar-docs\ndescription: Revise.\nauthor: [segredo-ficticio]",
        "name: revisar-docs\ndescription: [segredo-ficticio]",
        "name: outro\ndescription: Revise.",
        "name: revisar-docs\ndescription: " + "x" * 61,
        "[segredo-ficticio",
    ],
)
def test_frontmatter_invalido_sem_vazar_conteudo(tmp_path, header):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    write_skill(tmp_path, text=f"---\n{header}\n---\nsegredo-ficticio\n")
    with pytest.raises(SkillSelectionError) as exc:
        load_selected_skills(tmp_path, ["revisar-docs"])
    assert "segredo-ficticio" not in str(exc.value)
    assert str(tmp_path) not in str(exc.value)


@pytest.mark.parametrize("data", [b"\xff", skill_text(body=" ").encode(), b"sem frontmatter"])
def test_corpo_ou_encoding_invalidos(tmp_path, data):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    write_skill(tmp_path).write_bytes(data)
    with pytest.raises(SkillSelectionError):
        load_selected_skills(tmp_path, ["revisar-docs"])


@pytest.mark.parametrize("size,allowed", [(65536, True), (65537, False)])
def test_limite_de_arquivo_em_bytes(tmp_path, size, allowed):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    prefix = skill_text(body="")
    text = prefix + "á" * ((size - len(prefix.encode())) // 2)
    text += "x" * (size - len(text.encode()))
    write_skill(tmp_path, text=text)
    if allowed:
        assert load_selected_skills(tmp_path, ["revisar-docs"])[0].text == text
    else:
        with pytest.raises(SkillSelectionError):
            load_selected_skills(tmp_path, ["revisar-docs"])


@pytest.mark.parametrize("count,allowed", [(8, True), (9, False)])
def test_limite_de_nomes_unicos(tmp_path, count, allowed):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    names = [f"skill-{i}" for i in range(count)]
    for name in names:
        write_skill(tmp_path, name)
    if allowed:
        assert len(load_selected_skills(tmp_path, names * 2)) == count
    else:
        with pytest.raises(SkillSelectionError):
            load_selected_skills(tmp_path, names)


@pytest.mark.parametrize("extra,allowed", [(0, True), (1, False)])
def test_limite_agregado(tmp_path, extra, allowed):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    names = ["primeira", "segunda", "terceira"]
    for name, size in zip(names, [60000, 60000, 11072 + extra], strict=True):
        prefix = skill_text(name)
        write_skill(tmp_path, name, prefix + "x" * (size - len(prefix.encode())))
    if allowed:
        assert len(load_selected_skills(tmp_path, names)) == 3
    else:
        with pytest.raises(SkillSelectionError):
            load_selected_skills(tmp_path, names)


@pytest.mark.parametrize("component", ["root", "directory", "file", "hardlink"])
def test_links_recusados(tmp_path, component):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    outside = tmp_path / "externo"
    source = write_skill(outside)
    root = tmp_path / "home"
    root.mkdir()
    if component == "root":
        (root / "skills").symlink_to(outside / "skills", target_is_directory=True)
    elif component == "directory":
        (root / "skills").mkdir()
        (root / "skills" / "revisar-docs").symlink_to(source.parent, target_is_directory=True)
    else:
        path = write_skill(root)
        path.unlink()
        if component == "file":
            path.symlink_to(source)
        else:
            os.link(source, path)
    with pytest.raises(SkillSelectionError):
        load_selected_skills(root, ["revisar-docs"])
    assert source.read_text() == skill_text()


def test_fifo_recusado_sem_bloquear(tmp_path):
    path = write_skill(tmp_path)
    path.unlink()
    os.mkfifo(path)
    code = """
import sys
from pathlib import Path
from kairos_skills.runtime import SkillSelectionError, load_selected_skills
try:
    load_selected_skills(Path(sys.argv[1]), ['revisar-docs'])
except SkillSelectionError:
    print('recusado')
else:
    raise AssertionError('FIFO aceito')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "recusado"


def test_substituicao_do_nome_nao_redireciona_fd(tmp_path, monkeypatch):
    from kairos_skills.runtime import load_selected_skills

    path = write_skill(tmp_path)
    outside = tmp_path / "fora.txt"
    outside.write_text("fora")
    original_open = os.open

    def open_and_swap(name, *args, **kwargs):
        fd = original_open(name, *args, **kwargs)
        if name == "SKILL.md":
            path.rename(path.with_name("original.md"))
            path.symlink_to(outside)
        return fd

    monkeypatch.setattr(os, "open", open_and_swap)
    assert load_selected_skills(tmp_path, ["revisar-docs"])[0].text == skill_text()


def test_mudanca_durante_leitura_recusada(tmp_path, monkeypatch):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    path = write_skill(tmp_path)
    original_read = os.read
    changed = False

    def read_and_change(fd, count):
        nonlocal changed
        data = original_read(fd, count)
        if not changed:
            with path.open("a") as stream:
                stream.write("alterado")
            changed = True
        return data

    monkeypatch.setattr(os, "read", read_and_change)
    with pytest.raises(SkillSelectionError):
        load_selected_skills(tmp_path, ["revisar-docs"])


def test_colecao_invalida_recusada():
    from kairos_skills.runtime import SkillSelectionError, SkillSnapshot, validate_skill_snapshots

    snapshot = SkillSnapshot("revisar-docs", skill_text())
    for values in ([snapshot, snapshot], ["revisar-docs"], [{"name": "revisar-docs"}]):
        with pytest.raises(SkillSelectionError):
            validate_skill_snapshots(values)


def test_yaml_profundamente_aninhado_recusado_sem_traceback(tmp_path):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    nested = "[" * 1500 + "privado-ficticio" + "]" * 1500
    write_skill(
        tmp_path,
        text=f"---\nname: revisar-docs\ndescription: Revise.\nextra: {nested}\n---\ncorpo\n",
    )
    with pytest.raises(SkillSelectionError) as exc:
        load_selected_skills(tmp_path, ["revisar-docs"])
    assert "privado-ficticio" not in str(exc.value)


@pytest.mark.parametrize(
    "value", ["!!int segredo-ficticio", "2026-99-99", "9" * 5000], ids=["tag", "data", "inteiro"]
)
def test_conversao_yaml_invalida_recusada_sem_expor_valor(tmp_path, value):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    write_skill(
        tmp_path,
        text=f"---\nname: revisar-docs\ndescription: Revise.\nextra: {value}\n---\ncorpo\n",
    )
    with pytest.raises(SkillSelectionError) as exc:
        load_selected_skills(tmp_path, ["revisar-docs"])
    assert "segredo-ficticio" not in str(exc.value)


def test_aliases_yaml_nao_expandem_sem_limite(tmp_path):
    header = ["name: revisar-docs", "description: Revise.", "base: &base {k: v}"]
    previous = "base"
    for index in range(7):
        current = f"level{index}"
        aliases = ", ".join([f"*{previous}"] * 10)
        header.append(f"{current}: &{current} {{<<: [{aliases}]}}")
        previous = current
    write_skill(tmp_path, text="---\n" + "\n".join(header) + "\n---\ncorpo\n")
    code = """
import resource, sys
from pathlib import Path
from kairos_skills.runtime import SkillSelectionError, load_selected_skills
resource.setrlimit(resource.RLIMIT_AS, (192 * 1024 * 1024, 192 * 1024 * 1024))
try:
    load_selected_skills(Path(sys.argv[1]), ['revisar-docs'])
except SkillSelectionError:
    print('recusado')
else:
    raise AssertionError('expansão de aliases aceita')
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "recusado"


@pytest.mark.parametrize(
    "extra",
    ["[" * 65 + "x" + "]" * 65, "{" + ",".join(f"k{i}: x" for i in range(1100)) + "}"],
    ids=["profundidade", "nos"],
)
def test_complexidade_yaml_recusada(tmp_path, extra):
    from kairos_skills.runtime import SkillSelectionError, load_selected_skills

    write_skill(
        tmp_path,
        text=f"---\nname: revisar-docs\ndescription: Revise.\nextra: {extra}\n---\ncorpo\n",
    )
    with pytest.raises(SkillSelectionError):
        load_selected_skills(tmp_path, ["revisar-docs"])
