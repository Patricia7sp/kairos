"""Protocolo JSON-RPC stdio e o registro deferido de handlers.

`_reversa_sdd/ui-tui/` §2 (Tarefa 16).
"""

from __future__ import annotations

import types
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = ["DuplicateMethod", "HandlerRegistry", "method", "profile_scoped"]


class DuplicateMethod(ValueError):
    """Dois handlers com o mesmo nome de método."""


@dataclass
class HandlerRegistry:
    """Registro **deferido**, por um motivo mecânico preciso.

    `@method(nome)` apenas **enfileira**. É `install(server)` que reconstrói
    cada função com o `globals()` do servidor, via
    `types.FunctionType(fn.__code__, vars(server), ...)`.

    Isso é o que permitiu quebrar um `server.py` gigante em módulos **sem
    trocar o modelo de escopo por globais**: os handlers continuam enxergando
    os globais do servidor como antes de serem movidos. A alternativa —
    passar tudo por argumento — teria exigido reescrever os 156 handlers de
    uma vez.

    `__defaults__`, `__kwdefaults__`, `__closure__`, `__doc__` e `__dict__`
    são preservados: perder qualquer um mudaria o comportamento do handler de
    forma silenciosa.
    """

    _pending: dict[str, Callable] = field(default_factory=dict)

    def method(self, name: str) -> Callable[[Callable], Callable]:
        def decorator(fn: Callable) -> Callable:
            if name in self._pending:
                raise DuplicateMethod(
                    f"método {name!r} já registrado por {self._pending[name].__name__!r}"
                )
            self._pending[name] = fn
            return fn

        return decorator

    def install(self, server: Any) -> dict[str, Callable]:
        """Reconstrói cada handler com os globais do servidor."""
        instalados: dict[str, Callable] = {}
        for nome, fn in self._pending.items():
            rebound = types.FunctionType(
                fn.__code__, vars(server), fn.__name__, fn.__defaults__, fn.__closure__
            )
            rebound.__kwdefaults__ = fn.__kwdefaults__
            rebound.__doc__ = fn.__doc__
            rebound.__dict__.update(fn.__dict__)
            instalados[nome] = rebound
        return instalados

    @property
    def pending_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._pending))


def profile_scoped(fn: Callable) -> Callable:
    """Marca o handler para receber escopo de perfil no install."""
    fn._kairos_profile_scoped = True  # type: ignore[attr-defined]
    return fn


def method(registry: HandlerRegistry, name: str) -> Callable[[Callable], Callable]:
    return registry.method(name)
