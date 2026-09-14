"""patch: substituição exata/difusa com segurança, e lote SEARCH/REPLACE."""

import asyncio

from kairos_tools import file_patch as fp
from kairos_tools.file_patch import best_fuzzy_span, patch_tool

CONTEUDO = """def saudar(nome):
    return f'olá, {nome}'


def despedir(nome):
    print(f'tchau, {nome}')
"""


def _run(**kwargs):
    return asyncio.run(patch_tool(**kwargs))


def test_substituicao_exata_unica(tmp_path):
    arquivo = tmp_path / "a.py"
    arquivo.write_text(CONTEUDO, encoding="utf-8")
    result = _run(mode="replace", path=str(arquivo), old_string="tchau", new_string="até logo")
    assert result["success"] is True
    assert "até logo" in arquivo.read_text(encoding="utf-8")
    assert "tchau" not in arquivo.read_text(encoding="utf-8")


def test_substituicao_de_apagar_com_new_string_vazio(tmp_path):
    arquivo = tmp_path / "b.txt"
    arquivo.write_text("linha 1\nremover esta\nlinha 3\n", encoding="utf-8")
    result = _run(mode="replace", path=str(arquivo), old_string="remover esta\n", new_string="")
    assert result["success"] is True
    assert "remover esta" not in arquivo.read_text(encoding="utf-8")


def test_trecho_ambiguo_nao_aplica_nada(tmp_path):
    arquivo = tmp_path / "c.txt"
    arquivo.write_text("x\ny\nx\n", encoding="utf-8")
    result = _run(mode="replace", path=str(arquivo), old_string="x", new_string="z")
    assert result["success"] is False
    assert arquivo.read_text(encoding="utf-8") == "x\ny\nx\n"


def test_replace_all_troca_todas_as_ocorrencias(tmp_path):
    arquivo = tmp_path / "d.txt"
    arquivo.write_text("a a a", encoding="utf-8")
    result = _run(
        mode="replace", path=str(arquivo), old_string="a", new_string="b", replace_all=True
    )
    assert result["success"] is True
    assert result["replaced"] == 3
    assert arquivo.read_text(encoding="utf-8") == "b b b"


def test_fallback_difuso_absorve_deriva_de_espaco_no_fim_da_linha(tmp_path):
    arquivo = tmp_path / "e.py"
    arquivo.write_text("def foo():\n    return 0\n", encoding="utf-8")
    result = _run(
        mode="replace",
        path=str(arquivo),
        old_string="def foo():\n    return 0   \n",
        new_string="def foo(n):\n    return n\n",
    )
    assert result["success"] is True
    assert result.get("fuzzy_used") is True
    conteudo = arquivo.read_text(encoding="utf-8")
    assert "def foo(n):" in conteudo
    assert "    return n" in conteudo


def test_difuso_ambiguo_nao_e_adivinhacao(tmp_path):
    arquivo = tmp_path / "f.txt"
    arquivo.write_text(
        "primeira versão do trecho\noutros dados\nprimeira versão do trecho\n", encoding="utf-8"
    )
    result = _run(
        mode="replace",
        path=str(arquivo),
        old_string="primeira versão do trecho  ",
        new_string="nova",
    )
    assert result["success"] is False
    assert "primeira versão do trecho\n" in arquivo.read_text(encoding="utf-8")


def test_best_fuzzy_span_unitario():
    span = best_fuzzy_span("def foo():\n    return 0\n", "def foo():\n    return 0   \n")
    assert span is not None
    start, end, ratio = span
    assert start < end
    assert ratio >= fp.FUZZY_THRESHOLD
    assert best_fuzzy_span("nada a ver aqui", "bloco ausente por completo") is None


def test_lote_v4a_aplica_blocos_no_arquivo_padrao(tmp_path):
    arquivo = tmp_path / "g.py"
    arquivo.write_text("a\nb\nc\n", encoding="utf-8")
    lote = (
        "<<<<<<< SEARCH\na\n=======\nA\n>>>>>>> REPLACE\n"
        "<<<<<<< SEARCH\nc\n=======\nC\n>>>>>>> REPLACE\n"
    )
    result = _run(mode="patch", path=str(arquivo), patch=lote)
    assert result["status"] == "ok"
    assert result["applied"] == 2
    assert arquivo.read_text(encoding="utf-8") == "A\nb\nC\n"


def test_lote_v4a_multiarquivo_respeita_order(tmp_path):
    um = tmp_path / "um.txt"
    dois = tmp_path / "dois.txt"
    um.write_text("velho\n", encoding="utf-8")
    dois.write_text("velho\n", encoding="utf-8")
    lote = (
        f"<<<<<<< SEARCH {dois}\nvelho\n=======\ndois-novo\n>>>>>>> REPLACE\n"
        f"<<<<<<< SEARCH {um}\nvelho\n=======\num-novo\n>>>>>>> REPLACE\n"
    )
    result = _run(mode="patch", patch=lote)
    assert result["status"] == "ok"
    assert result["applied"] == 2
    assert um.read_text(encoding="utf-8") == "um-novo\n"
    assert dois.read_text(encoding="utf-8") == "dois-novo\n"


def test_lote_com_bloco_nao_confiavel_reporta_erro_e_nao_grava(tmp_path):
    arquivo = tmp_path / "h.txt"
    arquivo.write_text("ok\n", encoding="utf-8")
    lote = "<<<<<<< SEARCH\npresente\n=======\nnada\n>>>>>>> REPLACE\n"
    result = _run(mode="patch", path=str(arquivo), patch=lote)
    assert result["status"] == "ok"
    assert result["applied"] == 0
    assert result["errors"]
    assert arquivo.read_text(encoding="utf-8") == "ok\n"


def test_argumentos_invalidos_sao_erro_sem_excecao(tmp_path):
    arquivo = tmp_path / "i.txt"
    arquivo.write_text("conteúdo\n", encoding="utf-8")
    casos = []
    for args in [
        {"mode": "mode_inventado", "path": str(arquivo)},
        {"mode": "replace", "path": str(arquivo)},
        {"mode": "replace", "path": str(arquivo), "old_string": "x"},
        {"mode": "patch"},
        {"mode": "patch", "path": str(arquivo), "patch": "<<<<<<< SEARCH\n===\n"},
        {"mode": "patch", "patch": "<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n"},
    ]:
        casos.append(_run(**args))
    for case in casos:
        assert case["status"] == "invalid_arguments", case
        assert case.get("error")
