"""Orçamento de resultado e spillover.

RF-10. `_reversa_sdd/tools/` §"limite de resultado" (Tarefa 08).

**O nome do legado é enganoso e não foi herdado.** A operação não é
"truncamento": é *spillover*. O resultado grande é preservado **por inteiro**
em disco e substituído no contexto por um preview mais o caminho. Truncar com
perda só acontece quando a gravação falha.

A distinção é a diferença entre "o modelo não vê tudo" e "o dado sumiu".
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DEFAULT_RESULT_SIZE_CHARS",
    "PREVIEW_CHARS",
    "SPILLOVER_MAX_AGE_HOURS",
    "ResultBudget",
    "SpilloverResult",
    "generate_preview",
    "prune_spillover",
    "spillover_dir",
]

#: Teto global, quando a ferramenta não declara o seu.
DEFAULT_RESULT_SIZE_CHARS = 50_000

#: Quanto do conteúdo vai para o contexto junto do caminho.
PREVIEW_CHARS = 2_000

#: Cache de spillover é efêmero por natureza — o dado canônico já está no
#: transcript ou no arquivo original.
SPILLOVER_MAX_AGE_HOURS = 72


def _kairos_home() -> Path:
    home = os.environ.get("KAIROS_HOME")
    return Path(home).expanduser() if home else Path.home() / ".kairos"


def spillover_dir() -> Path:
    """Lar canônico: **sempre** host-side, junto dos demais caches.

    Não no diretório temporário do SO e não dentro do sandbox. Guardar no
    sandbox quebraria sessões que nunca rodaram um comando de terminal —
    MCP-only, cron, gateway — que é exatamente o bug que o legado corrigiu ao
    mover isto para cá.
    """
    return _kairos_home() / "cache" / "spillover"


@dataclass(frozen=True)
class ResultBudget:
    """A cascata de três níveis.

    Ferramenta → chamador → global. Nesta ordem, porque quem conhece o
    tamanho típico do próprio resultado é a ferramenta; o chamador sabe do
    contexto da chamada; e o global é o último recurso.
    """

    tool_limit: int | None = None
    caller_default: int | None = None
    global_default: int = DEFAULT_RESULT_SIZE_CHARS

    def max_chars(self) -> int:
        if self.tool_limit is not None:
            return self.tool_limit
        if self.caller_default is not None:
            return self.caller_default
        return self.global_default


def generate_preview(content: str, max_chars: int = PREVIEW_CHARS) -> tuple[str, bool]:
    """Preview do começo do conteúdo. Devolve `(texto, foi_cortado)`."""
    if len(content) <= max_chars:
        return content, False
    return content[:max_chars], True


@dataclass(frozen=True)
class SpilloverResult:
    in_context: str
    path: Path | None
    spilled: bool
    #: Verdadeiro quando a gravação falhou e houve perda de fato.
    truncated_lossy: bool = False


def maybe_spill(
    content: str,
    tool_use_id: str,
    budget: ResultBudget,
    *,
    now: float | None = None,
) -> SpilloverResult:
    """Preserva o resultado grande em disco, devolvendo preview + caminho.

    Se a gravação falhar — disco cheio, permissão — degrada para truncamento
    **com perda**, que é pior mas ainda melhor que estourar a janela de
    contexto ou derrubar o turno.
    """
    limit = budget.max_chars()
    if len(content) <= limit:
        return SpilloverResult(in_context=content, path=None, spilled=False)

    target = spillover_dir() / f"{_safe_name(tool_use_id)}.txt"
    preview, _ = generate_preview(content)

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError:
        return SpilloverResult(
            in_context=(
                preview + f"\n\n[RESULTADO TRUNCADO COM PERDA — {len(content):,} chars no total; "
                "não foi possível gravar o spillover]"
            ),
            path=None,
            spilled=False,
            truncated_lossy=True,
        )

    return SpilloverResult(
        in_context=(
            preview + f"\n\n[RESULTADO COMPLETO PRESERVADO — {len(content):,} chars em {target}]"
        ),
        path=target,
        spilled=True,
    )


def _safe_name(tool_use_id: str) -> str:
    """`tool_use_id` vem do provedor: nunca é caminho confiável."""
    keep = [c if (c.isalnum() or c in "-_") else "_" for c in tool_use_id]
    return ("".join(keep) or "sem_id")[:128]


def prune_spillover(
    max_age_hours: int = SPILLOVER_MAX_AGE_HOURS, *, now: float | None = None
) -> int:
    """Remove spillover velho. Devolve quantos arquivos saíram."""
    root = spillover_dir()
    if not root.is_dir():
        return 0
    moment = now if now is not None else time.time()
    cutoff = moment - max_age_hours * 3600
    removidos = 0
    for path in root.glob("*.txt"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removidos += 1
        except OSError:
            continue
    return removidos
