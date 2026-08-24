"""Segmentação de lote: o que pode rodar em paralelo.

`agent/tool_dispatch_helpers.py:44-73` no legado — e o 🔴 da spec dizia que
este critério "não foi localizado" porque a busca olhou `tools/` e ele mora
em `agent/`. Corrigido em 2026-08-24 (Tarefa 08).

Quatro classes de ferramenta, e a terceira é onde está a sutileza.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

__all__ = [
    "NEVER_PARALLEL_TOOLS",
    "PARALLEL_SAFE_TOOLS",
    "PATH_SCOPED_READERS",
    "PATH_SCOPED_WRITERS",
    "Segment",
    "SegmentKind",
    "ToolCall",
    "segment_batch",
]

#: Interativas: qualquer uma no lote força o lote inteiro a sequencial. Duas
#: perguntas simultâneas ao usuário competem pelo mesmo terminal.
NEVER_PARALLEL_TOOLS: frozenset[str] = frozenset({"clarify"})

#: Somente leitura, sem estado mutável de sessão compartilhado.
PARALLEL_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "ha_get_state",
        "ha_list_entities",
        "ha_list_services",
        "image_generate",
        "read_file",
        "search_files",
        "session_search",
        "skill_view",
        "skills_list",
        "vision_analyze",
        "web_extract",
        "web_search",
    }
)

#: Escopadas por caminho: a admissão é decidida por **sobreposição**.
PATH_SCOPED_READERS: frozenset[str] = frozenset({"read_file", "search_files"})
PATH_SCOPED_WRITERS: frozenset[str] = frozenset({"write_file", "patch"})
PATH_SCOPED_TOOLS = PATH_SCOPED_READERS | PATH_SCOPED_WRITERS


class SegmentKind(StrEnum):
    PARALLEL = "parallel"
    SEQUENTIAL = "sequential"


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict


@dataclass(frozen=True)
class Segment:
    kind: SegmentKind
    calls: tuple[ToolCall, ...]


_PATCH_HEADER = re.compile(r"^(?:\+\+\+|---|\*\*\*)\s+(?:[ab]/)?(\S+)", re.MULTILINE)


def _reserved_paths(call: ToolCall) -> list[Path]:
    """Que caminhos esta chamada reserva.

    Para `patch(mode="patch")` (V4A), os caminhos vêm dos **cabeçalhos do
    corpo do patch**, não do argumento `path=`: o `path=` pode estar
    obsoleto, e reservar o caminho errado é pior que não reservar — dá a
    ilusão de ordenação sem a ordenação.
    """
    args = call.arguments
    if call.name == "patch" and args.get("mode") == "patch":
        body = str(args.get("patch") or args.get("body") or "")
        headers = _PATCH_HEADER.findall(body)
        if headers:
            return [Path(h).resolve() for h in headers if h != "/dev/null"]

    if call.name == "search_files":
        # A raiz de busca é reservada como leitor. Uma busca agrupada depois
        # de uma escrita naquela subárvore fica ORDENADA ATRÁS dela em vez de
        # correr contra ela.
        return [Path(str(args.get("path") or args.get("root") or ".")).resolve()]

    raw = args.get("path") or args.get("file_path") or args.get("file")
    return [Path(str(raw)).resolve()] if raw else [Path(".").resolve()]


def _overlaps(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def segment_batch(calls: list[ToolCall]) -> list[Segment]:
    """Divide o lote em corridas paralelas e trechos sequenciais.

    A regra que justifica toda a complexidade: impedir que um `search_files`
    ou `read_file` em lote observe estado **pré-mutação** quando o modelo os
    agrupa junto com o `patch`/`write_file` de que dependem — *a clássica
    corrida escrita→leitura do mesmo bloco*.

    Leitores compartilham subárvore entre si (leituras do mesmo arquivo
    comutam). Um **escritor conflita com qualquer** reserva sobreposta, seja
    leitor ou escritor.
    """
    segments: list[list] = []
    current: list[ToolCall] = []
    reserved: list[tuple[Path, bool]] = []  # (caminho, é_escritor)

    def close_parallel() -> None:
        nonlocal current, reserved
        if current:
            segments.append([SegmentKind.PARALLEL, current])
            current = []
            reserved = []

    def add_sequential(call: ToolCall) -> None:
        close_parallel()
        if segments and segments[-1][0] is SegmentKind.SEQUENTIAL:
            segments[-1][1].append(call)
        else:
            segments.append([SegmentKind.SEQUENTIAL, [call]])

    for call in calls:
        if call.name in NEVER_PARALLEL_TOOLS:
            add_sequential(call)
            continue

        if call.name in PATH_SCOPED_TOOLS:
            is_writer = call.name in PATH_SCOPED_WRITERS
            paths = _reserved_paths(call)
            conflita = any(
                _overlaps(p, r) and (is_writer or r_is_writer)
                for p in paths
                for r, r_is_writer in reserved
            )
            if conflita:
                # Fecha a corrida: a chamada conflitante começa uma NOVA,
                # depois que a primeira terminar.
                close_parallel()
            current.append(call)
            reserved.extend((p, is_writer) for p in paths)
            continue

        if call.name in PARALLEL_SAFE_TOOLS:
            current.append(call)
            continue

        # Tudo o mais é barreira.
        add_sequential(call)

    close_parallel()

    # Corrida com menos de duas chamadas não tem ganho de concorrência, e o
    # executor sequencial tem despacho inline mais rico.
    normalizados: list[list] = []
    for kind_bruto, batch in segments:
        kind = (
            SegmentKind.SEQUENTIAL
            if kind_bruto is SegmentKind.PARALLEL and len(batch) < 2
            else kind_bruto
        )
        if (
            normalizados
            and normalizados[-1][0] is SegmentKind.SEQUENTIAL
            and kind is SegmentKind.SEQUENTIAL
        ):
            normalizados[-1][1].extend(batch)
        else:
            normalizados.append([kind, list(batch)])

    return [Segment(kind=k, calls=tuple(b)) for k, b in normalizados]
