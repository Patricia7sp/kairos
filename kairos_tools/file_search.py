"""Busca em arquivos por conteúdo (grep) ou por nome (find).

Réplica da ferramenta `search_files`/`n` do legado (specs `_reversa_sdd/`),
stdlib-only: aqui não há ripgrep nem `find` — a busca é em Python puro para
rodar igual em qualquer imagem. A interface é a mesma do legado: padrões de
regex por conteúdo e glob por nome, modos de saída, filtro por extensão,
paginação e linhas de contexto.
"""

from __future__ import annotations

import fnmatch
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Caminhos que toda busca relevante ignora — espelha o default de `rg`
#: (ignora `.git` e VCS ocultos) com o que um bytecode/história de hoje
#: adiciona. Não é fronteira de segurança: é sanidade de custo.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        "node_modules",
        ".venv",
        "venv",
        ".uv",
        ".direnv",
    }
)

#: Tetos de custo para a busca não correr o repositório inteiro de apartamento.
MAX_SCANNED_FILES = 20_000
MAX_BYTES_PER_FILE = 1_048_576  # 1 MiB
MAX_LIMIT = 500
DEFAULT_LIMIT = 50

_TARGETS = {"content", "files"}
_OUTPUT_MODES = {"content", "files_only", "count"}


@dataclass(frozen=True)
class SearchParams:
    """Um pedido de busca validado; imutável para custar pouco em each fork."""

    pattern: str
    target: str
    path: str
    file_glob: str | None
    limit: int
    offset: int
    output_mode: str
    context: int


async def search_files_tool(
    pattern: str,
    *,
    target: str = "content",
    path: str = ".",
    file_glob: str | None = None,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    output_mode: str = "content",
    context: int = 0,
) -> dict[str, Any]:
    """Busca conteúdo de arquivos (grep) ou arquivos por nome (find)."""
    parsed = _parse_arguments(
        pattern,
        target=target,
        path=path,
        file_glob=file_glob,
        limit=limit,
        offset=offset,
        output_mode=output_mode,
        context=context,
    )
    if isinstance(parsed, dict):
        return parsed
    return _run(parsed)


def _parse_arguments(
    pattern: Any,
    *,
    target: Any,
    path: Any,
    file_glob: Any,
    limit: Any,
    offset: Any,
    output_mode: Any,
    context: Any,
) -> SearchParams | dict[str, Any]:
    if not isinstance(target, str):
        return _invalid("target deve ser uma string.")
    if target not in _TARGETS:
        aliases = {"grep": "content", "find": "files"}
        target = aliases.get(target, target)
        if target not in _TARGETS:
            return _invalid("target deve ser 'content' ou 'files'.")
    if output_mode not in _OUTPUT_MODES:
        return _invalid("output_mode deve ser 'content', 'files_only' ou 'count'.")
    if type(context) is not int or context < 0 or context > 15:
        return _invalid("context deve ser um inteiro entre 0 e 15.")
    if file_glob is not None and (not isinstance(file_glob, str) or not file_glob.strip()):
        return _invalid("file_glob deve ser uma string não vazia.")
    if not isinstance(pattern, str):
        return _invalid("O padrão de busca deve ser uma string.")
    if not pattern.strip() and target != "files":
        return _invalid("Informe um padrão de busca não vazio.")
    if type(limit) is not int or not 0 < limit <= MAX_LIMIT:
        return _invalid(f"limit deve ser um inteiro entre 1 e {MAX_LIMIT}.")
    if type(offset) is not int or offset < 0:
        return _invalid("offset deve ser um inteiro >= 0.")
    if not isinstance(path, str):
        return _invalid("path deve ser uma string.")
    return SearchParams(pattern, target, path, file_glob, limit, offset, output_mode, context)


def _invalid(message: str) -> dict[str, Any]:
    return {"status": "invalid_arguments", "error": message}


def _run(params: SearchParams) -> dict[str, Any]:
    root = Path(os.path.expanduser(params.path))
    if not root.exists():
        return _invalid(f"Diretório não encontrado: {params.path}")
    if params.target == "content" and not root.is_dir():
        return _invalid(f"O caminho não é um diretório: {params.path}")
    if params.target == "files" and root.is_file():
        root = root.parent
    root = root.resolve()

    if params.target == "files":
        return _search_by_name(root, params)

    if params.target == "content":
        return _search_by_content(root, params)
    return _invalid("target deve ser 'content' ou 'files'.")


def _candidate_files(root: Path, file_glob: str | None) -> list[Path]:
    """Caminhos a procurar, determinístico e limitado por custo."""
    seen: list[Path] = []
    stack = [root]
    while stack and len(seen) < MAX_SCANNED_FILES:
        current = stack.pop()
        if current.name in SKIP_DIRS:
            continue
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if len(seen) >= MAX_SCANNED_FILES:
                break
            name = entry.name
            if entry.is_dir() and not entry.is_symlink():
                if name not in SKIP_DIRS:
                    stack.append(entry)
            elif file_glob is None or fnmatch.fnmatch(name, file_glob):
                seen.append(entry)
    return seen


def _search_by_name(root: Path, params: SearchParams) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    scanned = 0
    stack = [root]
    while stack and len(results) < params.offset + params.limit:
        current = stack.pop()
        if current.name in SKIP_DIRS:
            continue
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if len(results) >= params.offset + params.limit:
                break
            if entry.is_dir() and not entry.is_symlink() and entry.name not in SKIP_DIRS:
                stack.append(entry)
                continue
            scanned += 1
            rel = str(entry.relative_to(root)) if entry.is_relative_to(root) else entry.name
            if (
                (not params.pattern)
                or fnmatch.fnmatch(entry.name, params.pattern)
                or fnmatch.fnmatch(rel, params.pattern)
            ):
                results.append(
                    {
                        "path": rel,
                        "is_dir": entry.is_dir(),
                        "size": entry.stat().st_size if entry.is_file() else 0,
                        "last_modified": time.strftime(
                            "%Y-%m-%dT%H:%M:%S", time.localtime(entry.stat().st_mtime)
                        ),
                    }
                )
    results.sort(key=lambda item: item["path"])
    page = results[params.offset : params.offset + params.limit]
    return {
        "status": "ok",
        "target": "files",
        "matches": page,
        "count": len(page),
        "truncated": len(results) > params.offset + params.limit,
    }


def _search_by_content(root: Path, params: SearchParams) -> dict[str, Any]:
    try:
        regex = re.compile(params.pattern)
    except re.error as exc:
        return _invalid(f"Padrão inválido: {exc}")

    matches: list[dict[str, Any]] = []
    total_hits = 0
    scanned_files = 0
    for candidate in _candidate_files(root, params.file_glob):
        scanned_files += 1
        try:
            size = candidate.stat().st_size
            if size < 0 or size > MAX_BYTES_PER_FILE:
                continue
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        file_matches, file_hits = _grep_lines(
            regex, candidate, root, lines, params=params, collected=matches
        )
        total_hits += file_hits
        matches.extend(file_matches)
        if len(matches) >= params.offset + params.limit:
            break

    matched = len(matches)
    truncated = total_hits > params.offset + params.limit or matched >= params.limit
    return {
        "status": "ok",
        "target": "content",
        "matches": matches,
        "count": matched,
        "scanned_files": scanned_files,
        "truncated": truncated,
    }


def _grep_lines(
    regex: re.Pattern[str],
    candidate: Path,
    root: Path,
    lines: list[str],
    *,
    params: SearchParams,
    collected: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    hits: list[int] = []
    for index, line in enumerate(lines):
        if regex.search(line):
            hits.append(index)

    if params.output_mode == "count":
        return (
            [{"path": str(candidate.relative_to(root)), "count": len(hits)}] if hits else [],
            len(hits),
        )
    if params.output_mode == "files_only":
        return (
            [{"path": str(candidate.relative_to(root))}] if hits else [],
            len(hits),
        )

    out: list[dict[str, Any]] = []
    for line_no in hits:
        if len(out) + len(collected) >= params.offset + params.limit:
            break
        if len(out) + len(collected) >= params.offset:
            entry: dict[str, Any] = {
                "path": str(candidate.relative_to(root)),
                "line": line_no + 1,
                "content": lines[line_no],
            }
            if params.context:
                entry["before"] = lines[max(0, line_no - params.context) : line_no]
                entry["after"] = lines[line_no + 1 : line_no + 1 + params.context]
            out.append(entry)
    return out, len(hits)


__all__ = [
    "MAX_BYTES_PER_FILE",
    "MAX_LIMIT",
    "MAX_SCANNED_FILES",
    "SKIP_DIRS",
    "SearchParams",
    "search_files_tool",
]
