"""Cliente auxiliar — um ROTEADOR, não um modelo.

`_reversa_sdd/agent/` (Tarefa 13).

Cinco consumidores compartilham uma cadeia de resolução única em vez de
duplicar lógica de fallback: compressão de contexto, busca em sessão, extração
web, análise de visão e visão de browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "TEXT_CHAIN",
    "VISION_CHAIN",
    "AuxiliaryConfig",
    "AuxiliaryTask",
    "Backend",
    "resolve_backend",
]


class AuxiliaryTask(StrEnum):
    CONTEXT_COMPRESSION = "context_compression"
    SESSION_SEARCH = "session_search"
    WEB_EXTRACTION = "web_extraction"
    VISION_ANALYSIS = "vision_analysis"
    BROWSER_VISION = "browser_vision"


class Backend(StrEnum):
    MAIN = "main"  # provedor+modelo principais do usuário
    OPENROUTER = "openrouter"
    NOUS_PORTAL = "nous_portal"
    CUSTOM_ENDPOINT = "custom_endpoint"
    NATIVE_ANTHROPIC = "native_anthropic"
    DIRECT_API_KEY = "direct_api_key"  # z.ai/GLM, Kimi, MiniMax
    NONE = "none"


#: Cadeia de texto, 7 degraus.
TEXT_CHAIN: tuple[Backend, ...] = (
    Backend.MAIN,
    Backend.OPENROUTER,
    Backend.NOUS_PORTAL,
    Backend.CUSTOM_ENDPOINT,
    Backend.NATIVE_ANTHROPIC,
    Backend.DIRECT_API_KEY,
    Backend.NONE,
)

#: Cadeia de visão, 6 degraus. O endpoint custom fica reservado a modelos
#: locais (Qwen-VL, LLaVA, Pixtral) e por isso vem depois do Anthropic.
VISION_CHAIN: tuple[Backend, ...] = (
    Backend.MAIN,
    Backend.OPENROUTER,
    Backend.NOUS_PORTAL,
    Backend.NATIVE_ANTHROPIC,
    Backend.CUSTOM_ENDPOINT,
    Backend.NONE,
)

#: **Codex OAuth NUNCA entra nas cadeias de fallback.** A OpenAI protege o
#: endpoint atrás de uma allow-list de modelos não documentada e móvel: "just
#: try Codex with a hardcoded model" apodrece sozinho. Ele entra apenas quando
#: é o provedor principal do usuário, ou quando o chamador o pede explicitamente
#: com modelo.
CODEX_EXCLUDED_FROM_FALLBACK = True


@dataclass
class AuxiliaryConfig:
    #: Restringe o fallback do degrau 2 a SKUs `:free`. Existe porque um
    #: fallback silencioso para modelo pago é a falha que mais surpreende: o
    #: usuário não pediu, não viu, e paga.
    free_only: bool = False
    openrouter_model: str | None = None
    available: set[Backend] = field(default_factory=set)
    #: Override por tarefa: `auxiliary.<task>.provider` + `.model`.
    per_task: dict[AuxiliaryTask, Backend] = field(default_factory=dict)


def resolve_backend(
    task: AuxiliaryTask, cfg: AuxiliaryConfig, *, vision: bool | None = None
) -> Backend:
    """Primeiro degrau disponível da cadeia adequada.

    O override por tarefa vence a cadeia inteira: quem o define sabe algo que
    a heurística não sabe.
    """
    if task in cfg.per_task:
        return cfg.per_task[task]

    e_visao = (
        vision
        if vision is not None
        else task in (AuxiliaryTask.VISION_ANALYSIS, AuxiliaryTask.BROWSER_VISION)
    )
    cadeia = VISION_CHAIN if e_visao else TEXT_CHAIN

    for backend in cadeia:
        if backend is Backend.NONE or backend in cfg.available:
            return backend
    return Backend.NONE
