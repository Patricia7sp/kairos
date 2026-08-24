"""Os quatro gatilhos de compactação e a invariante que os amarra.

`_reversa_sdd/agent/` (Tarefa 13).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "OVERFLOW_REASONS",
    "CompactionTrigger",
    "FailoverReason",
    "compaction_trigger",
    "is_output_cap_error",
]


class FailoverReason(StrEnum):
    LONG_CONTEXT_TIER = "long_context_tier"  # 429
    PAYLOAD_TOO_LARGE = "payload_too_large"  # 413
    CONTEXT_OVERFLOW = "context_overflow"
    OUTPUT_CAP = "output_cap"
    RATE_LIMIT = "rate_limit"
    AUTH = "auth"
    UNKNOWN = "unknown"


#: As três que caracterizam estouro de **entrada**.
OVERFLOW_REASONS: frozenset[FailoverReason] = frozenset(
    {
        FailoverReason.LONG_CONTEXT_TIER,
        FailoverReason.PAYLOAD_TOO_LARGE,
        FailoverReason.CONTEXT_OVERFLOW,
    }
)


class CompactionTrigger(StrEnum):
    """Quatro caminhos: três automáticos e um forçado."""

    PROACTIVE_PREFLIGHT = "proactive_preflight"  # limiar, antes da chamada
    POST_RESPONSE = "post_response"  # porta should_compress
    REACTIVE_OVERFLOW = "reactive_overflow"  # erro do provedor
    FORCED = "forced"  # /compress, force=True
    NONE = "none"


def is_output_cap_error(reason: FailoverReason) -> bool:
    """Erro de teto de **saída** — `max_tokens` grande demais.

    Não é estouro de entrada, e a distinção importa: a recuperação é um retry
    só de `max_tokens`, que não exige compressão nenhuma.
    """
    return reason is FailoverReason.OUTPUT_CAP


@dataclass(frozen=True)
class CompactionDecision:
    trigger: CompactionTrigger
    allowed: bool
    reason: str


def compaction_trigger(
    *,
    compression_enabled: bool,
    over_threshold: bool = False,
    post_response_needs: bool = False,
    failover: FailoverReason | None = None,
    forced: bool = False,
) -> CompactionDecision:
    """Decide se e por qual caminho a compactação dispara.

    **A invariante que amarra os quatro** (portada de
    anomalyco/opencode#30749): com `compression.enabled: false`, **nenhum**
    gatilho automático dispara — **inclusive os reativos**.

    Antes da correção, o limiar proativo respeitava a configuração mas um erro
    de estouro do provedor ainda comprimia e **rotacionava a sessão em
    silêncio**, passando por cima da escolha explícita do usuário. O
    comportamento correto é falhar com erro terminal, para que o usuário
    compacte manualmente, comece do zero, troque de modelo ou reduza anexos.
    """
    # `/compress` nunca passa por aqui no legado, e a isenção é registrada:
    # a força é do usuário, e a configuração não a limita.
    if forced:
        return CompactionDecision(CompactionTrigger.FORCED, True, "forçada pelo usuário")

    # Isenção explícita: teto de SAÍDA não é estouro de entrada. O retry de
    # `max_tokens` dispara mesmo com a compressão desligada.
    if failover is not None and is_output_cap_error(failover):
        return CompactionDecision(
            CompactionTrigger.NONE,
            False,
            "erro de teto de saída: a recuperação é retry de max_tokens, não compressão",
        )

    if not compression_enabled:
        if failover in OVERFLOW_REASONS:
            return CompactionDecision(
                CompactionTrigger.REACTIVE_OVERFLOW,
                False,
                "compressão desligada: nem o caminho reativo pode comprimir e "
                "rotacionar a sessão por cima da escolha do usuário. Use "
                "/compress, /new, um modelo de contexto maior, ou reduza anexos.",
            )
        return CompactionDecision(CompactionTrigger.NONE, False, "compressão desligada")

    if failover in OVERFLOW_REASONS:
        return CompactionDecision(
            CompactionTrigger.REACTIVE_OVERFLOW, True, f"estouro do provedor: {failover}"
        )
    if over_threshold:
        return CompactionDecision(
            CompactionTrigger.PROACTIVE_PREFLIGHT, True, "limiar de contexto atingido"
        )
    if post_response_needs:
        return CompactionDecision(CompactionTrigger.POST_RESPONSE, True, "should_compress")

    return CompactionDecision(CompactionTrigger.NONE, False, "nada a fazer")
