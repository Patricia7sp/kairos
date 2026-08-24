"""Os 37 hooks de ciclo de vida e o despacho.

`_reversa_sdd/plugins/` (Tarefa 10).

**Um plugin quebrado nunca derruba a sessão do usuário.** É o contrato do
módulo, e ele governa todas as decisões aqui.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "HOOK_FAMILIES",
    "VALID_HOOKS",
    "HookRegistry",
    "HookResult",
    "UnknownHook",
    "redact_provider_error",
]


class HookFamily(StrEnum):
    SESSION = "session"
    TOOL = "tool"
    LLM = "llm"
    STREAM = "stream"
    API = "api"
    SUBAGENT = "subagent"
    KANBAN = "kanban"
    OTHER = "other"


#: Os 37, agrupados. A família não é decoração: **streaming observa, não
#: transforma** — os hooks daquela família são disparados assincronamente
#: fora do caminho do token, com payloads imutáveis. Confundir observação com
#: transformação ali colocaria um plugin no caminho crítico de cada token.
HOOK_FAMILIES: dict[HookFamily, tuple[str, ...]] = {
    HookFamily.SESSION: (
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
    ),
    HookFamily.TOOL: (
        "pre_tool_call",
        "post_tool_call",
        "transform_tool_result",
        "transform_terminal_output",
    ),
    HookFamily.LLM: ("pre_llm_call", "post_llm_call", "transform_llm_output"),
    HookFamily.STREAM: (
        "on_stream_start",
        "on_stream_delta",
        "on_stream_end",
        "on_interim_message",
    ),
    HookFamily.API: (
        "pre_api_request",
        "post_api_request",
        "api_request_error",
        "transform_api_error_classification",
    ),
    HookFamily.SUBAGENT: ("subagent_start", "subagent_stop"),
    HookFamily.KANBAN: (
        "kanban_task_claimed",
        "kanban_task_completed",
        "kanban_task_blocked",
        "on_kanban_worker_spawned",
        "on_kanban_worker_exited",
        "on_kanban_worker_stale_claim",
        "on_kanban_task_updated",
        "on_kanban_dispatch_tick",
    ),
    HookFamily.OTHER: (
        "pre_verify",
        "pre_gateway_dispatch",
        "pre_approval_request",
        "post_approval_response",
        "pre_transcription",
        "on_skill_lifecycle",
        "gateway_platform_event",
        "pre_command",
    ),
}

VALID_HOOKS: frozenset[str] = frozenset(h for hs in HOOK_FAMILIES.values() for h in hs)

#: Hooks cujo payload é imutável: observam o stream, não o transformam.
OBSERVE_ONLY: frozenset[str] = frozenset(HOOK_FAMILIES[HookFamily.STREAM])


class UnknownHook(KeyError):
    """Nome de hook fora dos 37."""


@dataclass
class HookResult:
    """O que um despacho produziu.

    `skipped` guarda os resultados válidos que **perderam** o desempate. Eles
    não somem: dois plugins disputando a mesma classificação é sinal de
    conflito de configuração, e silenciá-lo faria o usuário depurar por
    adivinhação.
    """

    results: list[Any] = field(default_factory=list)
    failures: list[tuple[str, Exception]] = field(default_factory=list)
    skipped: list[Any] = field(default_factory=list)

    def first_valid(self, is_valid: Callable[[Any], bool]) -> Any | None:
        """*Run-all-then-pick-first*: o primeiro válido em ordem de registro."""
        vencedor = None
        for r in self.results:
            if not is_valid(r):
                continue
            if vencedor is None:
                vencedor = r
            else:
                self.skipped.append(r)
        if self.skipped:
            logger.warning(
                "%d resultado(s) válido(s) descartado(s) por conflito de hook; "
                "o primeiro registrado venceu",
                len(self.skipped),
            )
        return vencedor


#: Campos que podem carregar dump de provedor não redigido.
_SENSITIVE_ERROR_FIELDS = ("error_message", "error_body")


def redact_provider_error(payload: dict[str, Any]) -> dict[str, Any]:
    """Redige os campos que podem carregar dump cru do provedor.

    **Decisão do Kairos (fecha G-21).** No legado o contrato de privacidade
    existe apenas como cláusula de docstring — *"callbacks must not log or
    forward them without redaction"* — sem barreira técnica nenhuma. Uma
    convenção documentada protege contra o descuido honesto e contra nada
    mais.

    Aqui a redação é o **default**, e receber o dump cru exige que o plugin
    declare `requires_raw_error: true` no manifesto. Assim a capacidade fica
    auditável — dá para listar quem a pediu — em vez de ser universal e
    invisível.
    """
    limpo = dict(payload)
    for campo in _SENSITIVE_ERROR_FIELDS:
        if limpo.get(campo):
            limpo[campo] = "[REDIGIDO: o plugin não declarou requires_raw_error]"
    return limpo


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[str, Callable]]] = {}
        self._raw_error_plugins: set[str] = set()

    def register(self, hook: str, callback: Callable, *, plugin: str) -> None:
        if hook not in VALID_HOOKS:
            raise UnknownHook(
                f"hook desconhecido: {hook!r}. Os válidos são {len(VALID_HOOKS)}; "
                f"veja kairos_plugins.hooks.VALID_HOOKS"
            )
        self._hooks.setdefault(hook, []).append((plugin, callback))

    def allow_raw_error(self, plugin: str) -> None:
        """Marca um plugin como autorizado a receber o dump cru (G-21)."""
        self._raw_error_plugins.add(plugin)

    def registered(self, hook: str) -> list[str]:
        return [p for p, _ in self._hooks.get(hook, [])]

    def invoke_hook(self, hook: str, **kwargs: Any) -> HookResult:
        """Executa **todos** os callbacks, com falhas isoladas.

        *Run-all*, não *stop-at-first*: uma resposta cedo nunca impede os
        callbacks seguintes de rodar. Isso importa porque muitos hooks têm
        efeito colateral legítimo (telemetria, log), e curto-circuitar
        entregaria comportamento que depende da ordem de instalação.
        """
        if hook not in VALID_HOOKS:
            raise UnknownHook(f"hook desconhecido: {hook!r}")

        resultado = HookResult()
        payload_base = dict(kwargs)

        for plugin, callback in self._hooks.get(hook, []):
            payload = payload_base
            if (
                hook == "transform_api_error_classification"
                and plugin not in self._raw_error_plugins
            ):
                payload = redact_provider_error(payload_base)

            try:
                r = callback(**payload)
            except Exception as exc:  # noqa: BLE001
                # DELIBERADO e central: "a broken plugin can never break
                # error classification". Estreitar exigiria prever toda
                # exceção de todo plugin de terceiro.
                logger.warning("plugin %r falhou no hook %r: %s", plugin, hook, exc)
                resultado.failures.append((plugin, exc))
                continue
            if r is not None:
                resultado.results.append(r)

        return resultado
