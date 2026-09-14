"""Ferramenta `patch`: substituição exata ou difusa, simples ou em lote.

O legado aplicava nove estratégias de fuzzy matching; aqui o difuso é feito
com `difflib` da stdlib e a mesma regra de segurança: **só aplica quando a
correspondência é confiável** — um único melhor candidato e com folga sobre
os demais. Ambigüidade é erro, não adivinhação. Em lote, cada SEARCH/REPLACE
é aplicado no arquivo mais recente, um a um.
"""

from __future__ import annotations

import difflib
import os
import re
from pathlib import Path
from typing import Any

FUZZY_THRESHOLD = 0.75
FUZZY_MARGIN = 0.05

_SEARCH_RE = re.compile(r"^<<<<<<< SEARCH(?:[ \t]+(.*))?$", re.MULTILINE)
_DIVIDER_RE = re.compile(r"^======= *$", re.MULTILINE)
_REPLACE_RE = re.compile(r"^>>>>>>> REPLACE\s*$", re.MULTILINE)


def _find_exact(text: str, old_string: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(old_string, start)
        if index == -1:
            break
        spans.append((index, index + len(old_string)))
        start = index + len(old_string)
    return spans


def best_fuzzy_span(text: str, block: str) -> tuple[int, int, float] | None:
    """Melhor span confiável para `block` em `text`, ou `None` se ambíguo/ausente.

    Ancoragem por linha mais longa do bloco: procura no arquivo a linha-longa
    mais distintiva do bloco e expande a janela até cobrir o bloco inteiro,
    comparando por linhas `rstrip` (a deriva mais comum é espaço no fim da
    linha). Só um candidato isolado, acima do threshold e com folga sobre os
    demais, é aceito.
    """
    text_lines = text.splitlines()
    block_lines = block.splitlines() or [""]
    norm_text = [line.rstrip() for line in text_lines]
    norm_block = [line.rstrip() for line in block_lines]
    if len(norm_block) == 1 and not norm_block[0]:
        return None

    ordered = sorted(enumerate(norm_block), key=lambda item: -len(item[1]))
    for block_index, anchor in ordered:
        if len(anchor) < 4:
            break
        anchors = [k for k, line in enumerate(norm_text) if line == anchor]
        if not anchors:
            continue
        windows = _windows_from_anchors(norm_block, norm_text, anchors, block_index)
        if not windows:
            continue
        start, end, ratio = windows[0]
        if len(windows) > 1 and windows[0][2] - windows[1][2] < FUZZY_MARGIN:
            return None
        if start >= end:
            return None
        return (
            sum(len(line) + 1 for line in text_lines[:start]),
            sum(len(line) + 1 for line in text_lines[:end]),
            ratio,
        )

    return None


def _windows_from_anchors(
    norm_block: list[str], norm_text: list[str], anchors: list[int], block_index: int
) -> list[tuple[int, int, float]]:
    visited: set[tuple[int, int]] = set()
    windows: list[tuple[int, int, float]] = []
    for line_index in anchors:
        start = line_index - block_index
        end = start + len(norm_block)
        if start < 0 or end > len(norm_text) or (start, end) in visited:
            continue
        visited.add((start, end))
        ratio = difflib.SequenceMatcher(
            None, norm_block, norm_text[start:end], autojunk=False
        ).ratio()
        if ratio >= FUZZY_THRESHOLD:
            windows.append((start, end, ratio))
    windows.sort(key=lambda item: (-item[2], -(item[1] - item[0])))
    return windows


def _apply_block(text: str, old_string: str, new_string: str) -> tuple[str, int, bool]:
    """Aplica uma substituição única; devolve (texto, ocorrências, aplicou)."""
    spans = _find_exact(text, old_string)
    if len(spans) == 1:
        start, end = spans[0]
        return text[:start] + new_string + text[end:], 1, True
    if len(spans) > 1:
        return text, len(spans), False
    fuzzy = best_fuzzy_span(text, old_string)
    if fuzzy is None:
        return text, 0, False
    start, end, _ratio = fuzzy
    if not text[start:end]:
        return text, 0, False
    return text[:start] + new_string + text[end:], 0, True


def _read(path: str) -> tuple[str | None, str | None]:
    try:
        return Path(path).read_text(encoding="utf-8"), None
    except FileNotFoundError:
        return None, f"Arquivo não encontrado: {path}"
    except OSError as exc:
        return None, f"Erro ao ler arquivo: {exc}"


def _replace_in_file(path: str, old_string: str, new_string: str) -> dict[str, Any]:
    text, error = _read(path)
    if error:
        return {"path": path, "success": False, "error": error}
    assert text is not None
    new_text, occurrences, applied = _apply_block(text, old_string, new_string)
    if not applied:
        if occurrences:
            return {
                "path": path,
                "success": False,
                "error": f"O trecho alvo aparece {occurrences} vezes. Aumente o contexto ou use replace_all.",
            }
        return {
            "path": path,
            "success": False,
            "error": "Trecho alvo não encontrado (exato ou difuso confiável).",
            "fuzzy": True,
        }
    Path(path).write_text(new_text, encoding="utf-8")
    return {"path": path, "success": True, "replaced": True, "fuzzy_used": not occurrences}


def _replace_all_in_file(path: str, old_string: str, new_string: str) -> dict[str, Any]:
    text, error = _read(path)
    if error:
        return {"path": path, "success": False, "error": error}
    assert text is not None
    count = text.count(old_string)
    if not count:
        if best_fuzzy_span(text, old_string) is None:
            return {"path": path, "success": False, "error": "Trecho alvo não encontrado."}
        return {"path": path, "success": False, "error": "replace_all não aplica fallback difuso."}
    Path(path).write_text(text.replace(old_string, new_string), encoding="utf-8")
    return {"path": path, "success": True, "replaced": count, "replace_all": True}


def _parse_patch(patch: str) -> list[dict[str, Any]]:
    """Decompõe `patch` em blocos SEARCH/REPLACE com arquivo opcional por bloco."""
    blocks: list[dict[str, Any]] = []
    for chunk in _REPLACE_RE.split(patch):
        block_text = chunk.strip("\n")
        if not block_text.strip():
            continue
        search_match = _SEARCH_RE.search(block_text)
        if search_match is None:
            raise ValueError(
                "bloco sem '<<<<<<< SEARCH' (arquivo informado na própria linha do marcador)."
            )
        divider = _DIVIDER_RE.search(block_text, search_match.end())
        if divider is None:
            raise ValueError("bloco sem separador '======='.")
        old_string = block_text[search_match.end() : divider.start()].strip("\n")
        new_string = block_text[divider.end() :].strip("\n")
        if not old_string:
            raise ValueError("bloco SEARCH vazio.")
        blocks.append(
            {
                "file": (search_match.group(1) or "").strip(),
                "old_string": old_string,
                "new_string": new_string,
            }
        )
    if not blocks:
        raise ValueError("nenhum bloco SEARCH/REPLACE encontrado.")
    return blocks


def _group_blocks(
    blocks: list[dict[str, Any]], default_file: str | None, order: list[str] | None
) -> dict[str, list[dict[str, Any]]]:
    if order is None or not order:
        names = sorted({b["file"] or default_file or "" for b in blocks})
        if "" in names and default_file is None:
            raise ValueError("Blocos sem arquivo: informe path como arquivo padrão.")
    else:
        names = list(order)
        known = {b["file"] or default_file or "" for b in blocks}
        missing = [item for item in order if item not in known]
        if missing:
            raise ValueError(f"order lista arquivos ausentes no patch: {missing}")

    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in names}
    for block in blocks:
        grouped[block["file"] or default_file or ""].append(block)
    return grouped


def _apply_file_blocks(name: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    file_result: dict[str, Any] = {"path": name, "applied": 0, "errors": []}
    cached: str | None = None
    for block in blocks:
        if cached is None:
            cached, error = _read(name)
            if error:
                file_result["errors"].append(f"{name}: {error}")
                return file_result
        assert cached is not None
        next_text, occurrences, applied = _apply_block(
            cached, block["old_string"], block["new_string"]
        )
        if not applied:
            what = "difuso" if occurrences == 0 else f"exato ({occurrences} ocorrências)"
            file_result["errors"].append(
                f"bloco não aplicado em {name}: alvo {what} não confiável."
            )
            continue
        cached = next_text
        file_result["applied"] += 1
    if file_result["applied"]:
        Path(name).write_text(cached or "", encoding="utf-8")
    file_result["success"] = bool(file_result["applied"]) and not file_result["errors"]
    return file_result


def _dispatch_patch(patch: str, path: str | None, order: list[str] | None) -> dict[str, Any]:
    if not isinstance(patch, str) or not patch.strip():
        return {"status": "invalid_arguments", "error": "patch é obrigatório em mode='patch'."}
    if order is not None and (not isinstance(order, list) or not order):
        return {"status": "invalid_arguments", "error": "order deve ser uma lista não vazia."}
    try:
        blocks = _parse_patch(patch)
        default_file = os.path.expanduser(path) if path else None
        grouped = _group_blocks(blocks, default_file, order)
    except ValueError as exc:
        return {"status": "invalid_arguments", "error": f"Formato V4A inválido: {exc}"}

    results = [_apply_file_blocks(name, grouped[name]) for name in grouped]
    errors = [err for item in results for err in item["errors"]]
    return {
        "status": "ok",
        "files": results,
        "applied": sum(item["applied"] for item in results),
        "errors": errors,
    }


async def patch_tool(
    *,
    mode: str = "replace",
    path: str | None = None,
    old_string: str | None = None,
    new_string: str | None = None,
    replace_all: bool = False,
    patch: str | None = None,
    order: list[str] | None = None,
) -> dict[str, Any]:
    """Aplica uma substituição difusa/exata em um arquivo ou um lote V4A."""
    if mode not in ("replace", "patch"):
        return {"status": "invalid_arguments", "error": "mode deve ser 'replace' ou 'patch'."}

    if mode == "replace":
        if not isinstance(path, str) or not path.strip():
            return {"status": "invalid_arguments", "error": "path é obrigatório em mode='replace'."}
        if not isinstance(old_string, str) or not old_string:
            return {
                "status": "invalid_arguments",
                "error": "old_string é obrigatório em mode='replace'.",
            }
        if not isinstance(new_string, str):
            return {"status": "invalid_arguments", "error": "new_string deve ser uma string."}
        full_path = os.path.expanduser(path)
        if replace_all:
            return _replace_all_in_file(full_path, old_string, new_string)
        return _replace_in_file(full_path, old_string, new_string)

    return _dispatch_patch(patch or "", path, order)


__all__ = ["FUZZY_MARGIN", "FUZZY_THRESHOLD", "best_fuzzy_span", "patch_tool"]
