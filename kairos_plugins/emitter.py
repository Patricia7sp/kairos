"""O emissor: onde o runtime do Kairos dispara os hooks que tem emissor.

`kairos_plugins/hooks.py` sabe **executar** callbacks; este módulo sabe **quando
chamá-los**. A separação é deliberada: os 37 nomes e os contratos vêm da spec,
mas o ponto exato de emissão no núcleo está marcado 🔴 **não mapeado** lá
(`_reversa_sdd/plugins/design.md`). Então este lote emite um recorte honesto e
**diz qual é**: `EMITTED_HOOKS` é a lista do que dispara hoje, e o `kairos hooks
list` reporta os 31 restantes como "sem emissor" em vez de listá-los como se
funcionassem.

**Observação não vira intervenção.** Todo hook desta lista é de observação: o
retorno de um callback não muda parâmetro, argumento, resultado nem streaming do
turno. Os `transform_*` e o portão `pre_verify` ficam de fora — mudar o que o
modelo ou o usuário veem é outra decisão, com política própria (D-PLUG.2).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kairos_plugins.hooks import HookRegistry, UnknownHook

logger = logging.getLogger(__name__)

__all__ = ["EMITTED_HOOKS", "HookEmitter"]


#: Hooks que o runtime emite hoje. Fonte única para o emissor e para o relatório
#: do CLI — os dois leem daqui, então emitter e relatório não podem divergir.
EMITTED_HOOKS: frozenset[str] = frozenset(
    {
        # Ciclo da sessão/turno.
        "on_session_start",
        "on_session_end",
        # Ciclo da ferramenta, no caminho do Chat.
        "pre_tool_call",
        "post_tool_call",
        # Rodada do provedor.
        "pre_llm_call",
        "post_llm_call",
    }
)


class HookEmitter:
    """Despacho do runtime para os plugins, com custo zero quando ninguém escuta.

    Sem registro, ou sem callback para o hook pedido, `emit` **não faz nada** —
    nem `await` novo, nem alocação. É o que mantém a regra do cinto estreito:
    uma instalação sem plugins não paga nada por ter o emitter no caminho do
    turno.
    """

    def __init__(self, registry: HookRegistry | None = None) -> None:
        self._registry = registry

    @property
    def enabled(self) -> bool:
        """Há plugin escutando? Registro vazio é o mesmo que nenhum plugin."""
        return bool(self._registry is not None and self._registry.hooks_with_callbacks())

    def listeners(self, hook: str) -> tuple[str, ...]:
        """Plugins registrados neste hook, em ordem de registro."""
        if self._registry is None:
            return ()
        return tuple(self._registry.registered(hook))

    async def emit(self, hook: str, /, **payload: Any) -> None:
        """Dispara `hook` para os plugins registrados, fora do event loop.

        Os callbacks são **síncronos** por contrato (`HookRegistry.invoke_hook` é
        sync), então despachar direto seguraria o loop do turno — e a spec
        registra esse risco como aberto: *"um plugin lento em `pre_tool_call`
        degrada todo turno sem sinal"*. `asyncio.to_thread` mantém o loop livre;
        o isolamento de falha por callback continua sendo o do `invoke_hook`.

        Hook fora de `EMITTED_HOOKS` é **erro de programação** e levanta: emitir
        um hook que a lista diz que não tem emissor é exatamente a divergência que
        este lote existe para tornar visível.
        """
        if hook not in EMITTED_HOOKS:
            raise UnknownHook(
                f"{hook!r} não tem emissor neste build; os hooks emitidos são "
                f"{sorted(EMITTED_HOOKS)}"
            )
        registro = self._registry
        if registro is None or not registro.registered(hook):
            return
        await asyncio.to_thread(registro.invoke_hook, hook, **payload)
