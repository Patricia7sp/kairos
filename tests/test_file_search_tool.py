"""search_files: grep/find em arquivos, sem dependência de rg/find."""

import asyncio

from kairos_tools import file_search
from kairos_tools.file_search import search_files_tool

TREE = {
    "alpha.py": "def saudar(nome):\n    return f'olá, {nome}'\n\nprint(saudar('mundo'))\n",
    "beta.md": "# Manual\nConteúdo de referência.\n",
    "ignored.txt": "token secreto aqui\n",
    "nested/delta.py": "x = 1\ny = 2\n",
}


def _tree(tmp_path):
    for rel, content in TREE.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    dotgit = tmp_path / "sub" / ".git" / "config"
    dotgit.parent.mkdir(parents=True, exist_ok=True)
    dotgit.write_text("token escondido\n")
    return tmp_path


def _run(**kwargs):
    return asyncio.run(search_files_tool(**kwargs))


def test_busca_por_conteudo_linha_a_linha_com_numero(tmp_path):
    root = _tree(tmp_path)
    result = _run(pattern=r"saudar", path=str(root))

    assert result["status"] == "ok"
    assert result["target"] == "content"
    paths = [m["path"] for m in result["matches"]]
    assert "alpha.py" in paths
    assert "beta.md" not in paths
    linhas = {m["line"]: m["content"] for m in result["matches"]}
    assert linhas[4] == "print(saudar('mundo'))"


def test_filtro_por_glob_restringe_a_busca_conteudo(tmp_path):
    root = _tree(tmp_path)
    result = _run(pattern=r"token", path=str(root), file_glob="*.txt")
    assert [m["path"] for m in result["matches"]] == ["ignored.txt"]


def test_git_e_pulado_e_o_dotdir_nunca_vaza_na_resposta(tmp_path):
    root = _tree(tmp_path)
    assert _run(pattern="escondido", path=str(root))["matches"] == []
    result = _run(pattern="x", path=str(root))
    assert {m["path"] for m in result["matches"]} == {"nested/delta.py"}


def test_modo_count_e_files_only(tmp_path):
    root = _tree(tmp_path)
    counts = _run(pattern="saudar", path=str(root), output_mode="count")
    counts_map = {m["path"]: m["count"] for m in counts["matches"]}
    assert counts_map["alpha.py"] == 2

    names = _run(pattern="saudar", path=str(root), output_mode="files_only")
    assert names["matches"] == [{"path": "alpha.py"}]


def test_target_files_acha_por_glob_e_padrao_vazio_lista_tudo(tmp_path):
    root = _tree(tmp_path)
    result = _run(pattern="*.py", target="files", path=str(root))
    assert {m["path"] for m in result["matches"]} == {"alpha.py", "nested/delta.py"}

    tudo = _run(pattern="", target="files", path=str(root))
    nomes = {m["path"] for m in tudo["matches"]}
    assert {"alpha.py", "beta.md", "ignored.txt", "nested/delta.py"} <= nomes


def test_paginacao_offset_e_limit_e_disjunta(tmp_path):
    root = _tree(tmp_path)
    args = {"pattern": "", "target": "files", "path": str(root)}
    first = _run(**args, limit=2)
    second = _run(**args, limit=2, offset=2)
    assert len(first["matches"]) == 2
    assert len(second["matches"]) == 2
    first_set = {m["path"] for m in first["matches"]}
    assert not first_set & {m["path"] for m in second["matches"]}


def test_regex_invalida_e_argumentos_invalidos_sao_recusados(tmp_path):
    root = _tree(tmp_path)
    result = _run(pattern="(", path=str(root))
    assert result["status"] == "invalid_arguments"

    cases = [
        _run(pattern="", path=str(root)),
        _run(pattern="x", path=str(root), limit=0),
        _run(pattern="x", path=str(root), limit="5"),  # type: ignore[arg-type]
        _run(pattern="x", path=str(root), offset=-1),
        _run(pattern="x", path=str(root / "inexistente")),
        _run(pattern="x", target="errado", path=str(root)),
        _run(pattern="x", output_mode="tab", path=str(root)),
    ]
    for case in cases:
        assert case["status"] == "invalid_arguments", case
        assert case.get("error")


def test_context_inclui_linhas_adjacentes(tmp_path):
    root = _tree(tmp_path)
    result = _run(pattern="return", path=str(root), context=2)
    block = next(m for m in result["matches"] if m["path"] == "alpha.py")
    assert block["before"] == ["def saudar(nome):"]
    assert block["after"] == ["", "print(saudar('mundo'))"]


def test_constantes_de_custo_documentam_tetos():
    assert file_search.MAX_SCANNED_FILES >= 1000
    assert file_search.MAX_BYTES_PER_FILE == 1_048_576
    assert file_search.MAX_LIMIT == 500
