"""Registro de ferramentas — singleton de processo.

RF-01 a RF-06, RF-11 a RF-14. `_reversa_sdd/tools/` (Tarefa 08).

**Trocar toolsets no meio da conversa é proibido** (ADR 001): invalida o
prefixo de prompt cacheado e multiplica o custo do usuário. A composição do
toolset é decidida **antes** do turno, e este módulo não oferece nenhuma API
que a mude depois — a ausência é a garantia.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from kairos_tools.budget import ResultBudget

logger = logging.getLogger(__name__)

__all__ = [
    "ToolEntry",
    "ToolRegistry",
    "ToolsUnavailableUnderTest",
    "Toolset",
    "registry",
    "tool_error",
]

#: Nome da allowlist de sandbox para o shell → ferramenta real no despacho.
#: `terminal` não é registrado — o handler é o `bash` — então resolvê-lo aqui
#: dá o efeito de sandbox a quem chama pelo nome da política sem expor um
#: segundo schema (D-08.3).
SANDBOX_ALIASES: dict[str, str] = {"terminal": "bash"}


class ToolsUnavailableUnderTest(RuntimeError):
    """RF-14 — ferramentas bloqueadas sob pytest.

    Sem isto, um teste que rode o agente de verdade escreve arquivos, envia
    mensagens e agenda jobs no ambiente do desenvolvedor. O modo de falha é
    silencioso e externo ao processo.
    """


def tool_error(message: str) -> dict[str, str]:
    """Formato de erro **único**. Um handler que explode nunca derruba o turno."""
    return {"error": message}


@dataclass(frozen=True)
class ToolEntry:
    name: str
    handler: Callable[..., Any]
    schema: dict[str, Any]
    toolset: str = "core"
    is_async: bool = False
    max_result_size_chars: int | None = None
    #: Sobrescrita por plugin: guarda a identidade e a geração de quem a fez.
    override_of: str | None = None
    plugin_generation: int | None = None


@dataclass
class Toolset:
    name: str
    #: Requisito verificável de ambiente. Falso ⇒ nenhuma ferramenta do
    #: toolset é exposta ao modelo — omitida, não falhando na chamada.
    requirement: Callable[[], bool] | None = None
    aliases: set[str] = field(default_factory=set)

    def available(self) -> bool:
        if self.requirement is None:
            return True
        try:
            return bool(self.requirement())
        except Exception:  # noqa: BLE001
            # DELIBERADO: um requisito que levanta é um requisito não
            # satisfeito. Propagar transformaria a montagem do prompt inteiro
            # em falha por causa de uma sonda de ambiente.
            logger.warning(
                "requisito do toolset %r levantou; tratando como indisponível", self.name
            )
            return False


class ToolRegistry:
    """Singleton de processo. Não há registro por sessão nem por requisição."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._toolsets: dict[str, Toolset] = {"core": Toolset("core")}
        self._alias_targets: dict[str, str] = {}

    # -- registro ----------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Callable[..., Any],
        schema: dict[str, Any],
        *,
        toolset: str = "core",
        max_result_size_chars: int | None = None,
    ) -> ToolEntry:
        if toolset not in self._toolsets:
            self._toolsets[toolset] = Toolset(toolset)
        entry = ToolEntry(
            name=name,
            handler=handler,
            schema=schema,
            toolset=toolset,
            is_async=inspect.iscoroutinefunction(handler),
            max_result_size_chars=max_result_size_chars,
        )
        self._tools[name] = entry
        return entry

    def unregister(self, name: str) -> bool:
        return self._tools.pop(name, None) is not None

    def register_toolset(self, name: str, requirement: Callable[[], bool] | None = None) -> Toolset:
        ts = Toolset(name, requirement=requirement)
        self._toolsets[name] = ts
        return ts

    def register_toolset_alias(self, alias: str, target: str) -> None:
        if target not in self._toolsets:
            raise KeyError(f"toolset alvo desconhecido: {target!r}")
        self._alias_targets[alias] = target
        self._toolsets[target].aliases.add(alias)

    def get_toolset_alias_target(self, alias: str) -> str | None:
        return self._alias_targets.get(alias)

    # -- consulta ----------------------------------------------------------

    def get_all_tool_names(self) -> list[str]:
        return sorted(self._tools)

    def inventory(self) -> dict[str, Any]:
        """Describe current registrations without exposing or calling handlers.

        Availability reflects local toolset requirements, not a live health
        check of every remote service. Schemas are detached from registration
        so inventory consumers cannot change the model's tool definitions.
        """
        entries = sorted(self._tools.values(), key=lambda entry: entry.name)
        groups = sorted(self._toolsets.values(), key=lambda group: group.name)
        availability = {group.name: group.available() for group in groups}
        tools = []
        for entry in entries:
            schema = copy.deepcopy(entry.schema)
            definition = schema.get("function", schema)
            tools.append(
                {
                    "name": entry.name,
                    "toolset": entry.toolset,
                    "description": definition.get("description", ""),
                    "schema": schema,
                    "available": availability.get(entry.toolset, False),
                    "plugin": entry.override_of,
                }
            )
        return {
            "tools": tools,
            "total": len(tools),
            "available": sum(tool["available"] for tool in tools),
            "toolsets": [
                {
                    "name": group.name,
                    "enabled": availability[group.name],
                    "aliases": sorted(group.aliases),
                    "tools": [entry.name for entry in entries if entry.toolset == group.name],
                }
                for group in groups
            ],
        }

    def is_toolset_available(self, name: str) -> bool:
        target = self._alias_targets.get(name, name)
        ts = self._toolsets.get(target)
        return ts.available() if ts else False

    def check_tool_availability(self, name: str) -> bool:
        entry = self._tools.get(name)
        return bool(entry) and self.is_toolset_available(entry.toolset)

    def get_definitions(self) -> list[dict[str, Any]]:
        """O que o modelo **vê**.

        Ferramenta cujo requisito de ambiente falha é **omitida**, não
        exposta para falhar na chamada: um schema que o modelo pode chamar e
        que sempre erra é pior que ferramenta ausente — ele tenta, falha,
        e tenta de novo achando que errou os argumentos.
        """
        return [
            e.schema
            for e in sorted(self._tools.values(), key=lambda e: e.name)
            if self.is_toolset_available(e.toolset)
        ]

    def get_max_result_size(self, name: str, default: int | None = None) -> int:
        """A cascata de três níveis."""
        entry = self._tools.get(name)
        return ResultBudget(
            tool_limit=entry.max_result_size_chars if entry else None,
            caller_default=default,
        ).max_chars()

    # -- despacho ----------------------------------------------------------

    def _resolve_alias(self, name: str) -> str:
        """Nome da allowlist de sandbox → ferramenta registrada (D-08.3)."""
        return SANDBOX_ALIASES.get(name, name)

    def dispatch(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """Executa por nome. **Nunca levanta.**

        Toda exceção vira `{"error": ...}`. Ferramenta desconhecida também —
        é erro nomeado, não exceção, porque o modelo alucina nomes com
        frequência e isso é ocorrência normal, não falha do sistema.
        """
        if _under_test() and not _tools_allowed_under_test():
            raise ToolsUnavailableUnderTest(
                f"ferramenta {name!r} chamada sob pytest; use KAIROS_ALLOW_TOOLS_IN_TESTS=1 "
                "se for exatamente isso que o teste quer"
            )

        entry = self._tools.get(self._resolve_alias(name))
        if entry is None:
            return tool_error(f"Unknown tool: {name}")

        args = arguments or {}
        try:
            if entry.is_async:
                return _run_async(entry.handler(**args))
            return entry.handler(**args)
        except Exception as exc:
            # DELIBERADO: o contrato é que nenhum handler derruba o turno.
            # Estreitar aqui exigiria prever toda exceção de 127 ferramentas
            # e de todo plugin de terceiro.
            logger.warning("ferramenta %r levantou: %s", name, exc, exc_info=True)
            return tool_error(f"{type(exc).__name__}: {exc}")

    # -- snapshot ----------------------------------------------------------

    def snapshot_registration(self) -> dict[str, ToolEntry]:
        return dict(self._tools)

    def restore_registration(self, snapshot: dict[str, ToolEntry]) -> None:
        self._tools = dict(snapshot)

    # -- overrides de plugin ------------------------------------------------

    def apply_plugin_override(
        self, name: str, handler: Callable[..., Any], *, plugin: str, generation: int
    ) -> ToolEntry:
        """Sobrescreve uma ferramenta, **carregando identidade e geração**.

        Um override anônimo é irreversível na prática: não se sabe quem o
        fez nem a qual versão do plugin ele pertence, então não há como
        desfazê-lo seletivamente quando aquele plugin é removido.
        """
        base = self._tools.get(name)
        if base is None:
            raise KeyError(f"não há ferramenta {name!r} para sobrescrever")
        entry = replace(
            base,
            handler=handler,
            is_async=inspect.iscoroutinefunction(handler),
            override_of=plugin,
            plugin_generation=generation,
        )
        self._tools[name] = entry
        return entry


def _under_test() -> bool:
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def _tools_allowed_under_test() -> bool:
    return os.environ.get("KAIROS_ALLOW_TOOLS_IN_TESTS", "").lower() in {"1", "true", "yes"}


def _run_async(coro):
    """Bridge de handler assíncrono. O chamador não precisa saber qual é qual."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Já há loop rodando: executa num loop próprio numa thread, para não
    # reentrar no loop do chamador.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


#: O singleton.
registry = ToolRegistry()
