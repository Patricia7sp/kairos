"""Despachante da família `STREAM`: observar o stream sem poder transformá-lo.

O resto da Wiring de hooks (D-PLUG.2) emite por `await`: o runtime espera o
callback responder. Isso é aceitável em `pre_tool_call` — uma chamada por turno —
e **inaceitável por token**. A spec é explícita
(`_reversa_sdd/plugins/requirements.md:49`): a família stream é disparada
*assincronamente fora do caminho do token*, com payloads normalizados
imutáveis, e observa sem transformar. Um `await` por delta devolveria ao plugin
o controle do ritmo do stream.

O caminho do token aqui é `enqueue`: **síncrono, sem `await`, sem alocar quando
ninguém escuta.** O callback roda em uma thread daemon exclusiva por
(turno, hook, plugin), lendo de uma fila bounded própria. Três consequências que são
o contrato, não acidente de implementação:

1. **Observação não vira intervenção.** O runtime nunca entrega o objeto do
   stream: o callback recebe `callback(**item)`, e a dupla asterisco entrega um
   dict **novo, do callback**. Reescrever `payload["delta"]` muda só a cópia do
   plugin, e o texto que o usuário leu já estava decidido antes do despacho. É
   por construção, não por guarda: não há nada para o plugin alcançar.
2. **Fila cheia descarta o mais antigo e conta.** Enfileirar nunca bloqueia e
   nunca cresce sem limite. Um consumidor lento perde o passado, nunca o
   presente nem o `on_stream_end` — que é o que um observador de telemetria
   precisa preservar. Cada descarte conta e é logado esparso; descarte
   silencioso esconderia uma telemetria furada.
3. **O dreno é do turno, e tem timeout.** `aclose` entrega o que está na fila
   antes de parar cada worker, pelo mesmo caminho de cleanup persistente de
   D-PLUG.6. Callback que trava perde o worker com log; não segura o fim do
   turno.

**Ordem: dentro de um hook, sim; entre hooks, não.** Cada (hook, plugin) tem
um worker, e a fila dele preserva a ordem dos itens. Entre `on_stream_start`,
`on_stream_delta` e `on_stream_end` não há ordem — são workers distintos, em
threads distintas, e o de `delta` é de propósito mais lento que o de `end`. Um
observador que precisar de ordem usa o caminho awaited dos hooks de sessão.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from kairos_plugins.emitter import ENQUEUED_HOOKS
from kairos_plugins.hooks import HookRegistry, UnknownHook

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_QUEUE_SIZE", "StreamHookDispatcher"]


#: Fila por callback. O legado usa 1024 (`agent/plugin_stream_hooks.py:16`); o
#: ponto é ser **bounded**, não ser grande: um delta de texto ocupa uma string,
#: e o que protege o stream é o teto, não a folga.
DEFAULT_QUEUE_SIZE = 256

#: Sentinela de parada do worker. Vai para a cauda da fila de propósito: o worker
#: entrega tudo que já foi enfileirado antes de ver isto.
_STOP = object()


@dataclass
class _Consumer:
    """Um callback de plugin observing uma fila e um worker."""

    hook: str
    plugin: str
    callback: Callable[..., Any]
    queue: queue.Queue[Any]
    task: asyncio.Task[None] | None = None
    dropped: int = 0
    stopped: threading.Event = field(default_factory=threading.Event)


class StreamHookDispatcher:
    """Fila bounded por callback para a família stream, sem `await` no token."""

    def __init__(
        self,
        registry: HookRegistry | None = None,
        *,
        queue_size: int = DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._registry = registry
        self._queue_size = queue_size
        self._consumers: dict[tuple[str, str], _Consumer] = {}
        # Quem escuta, resolvido **uma vez**: registro de plugin acontece no
        # boot, não no meio do turno. Perguntar ao registro por delta aloca uma
        # lista por token; um frozenset responde sem alocar nada.
        self._listening: frozenset[str] = (
            frozenset(registry.hooks_with_callbacks()) if registry is not None else frozenset()
        )

    @property
    def enabled(self) -> bool:
        """Há plugin escutando stream? Registro vazio é o mesmo que nenhum."""
        return bool(self._listening)

    @property
    def dropped(self) -> int:
        """Quantos eventos foram descartados porque um consumer estava lento."""
        return sum(c.dropped for c in self._consumers.values())

    def listening(self, hook: str) -> bool:
        """Alguém registra callback para este hook? Custo de um lookup."""
        return hook in self._listening

    def for_turn(self) -> StreamHookDispatcher:
        """Compartilha o registro, mantendo filas e workers exclusivos do turno."""
        return StreamHookDispatcher(self._registry, queue_size=self._queue_size)

    def enqueue(self, hook: str, /, **payload: Any) -> bool:
        """Entrega `payload` a quem escuta `hook`. Síncrono e sem `await`.

        Devolve `False` quando ninguém escuta — o caso comum, e ele custa um
        dicionário e um `is None`: instalação sem plugins de stream não paga
        nada no caminho do token.
        """
        if hook not in ENQUEUED_HOOKS:
            raise UnknownHook(
                f"{hook!r} não é enfileirado; a família stream entra por "
                f"{type(self).__name__}.enqueue, e os enfileirados são "
                f"{sorted(ENQUEUED_HOOKS)}"
            )
        registro = self._registry
        if registro is None or hook not in self._listening:
            return False
        # O item é um dict nosso, e `callback(**item)` dá ao plugin um dict
        # **dele**. É essa cópia que garante a imutabilidade que importa: o
        # callback escreve onde quiser e o texto do stream já foi decidido.
        enfileirado = False
        for consumer in self._consumers_for(hook):
            enfileirado = self._put(consumer, dict(payload)) or enfileirado
        return enfileirado

    async def aclose(self, *, timeout: float = 2.0) -> None:
        """Drena o que está na fila e para cada worker, com teto de tempo."""
        consumers = list(self._consumers.values())
        if not consumers:
            return
        tasks = [asyncio.create_task(self._stop_consumer(consumer)) for consumer in consumers]
        self._consumers.clear()
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        if pending:
            logger.warning(
                "%d consumidor(es) de stream não drenaram em %.1fs; worker cancelado "
                "e o resto dos consumidores seguiu",
                len(pending),
                timeout,
            )
            for task in pending:
                task.cancel()
            for consumer in consumers:
                if consumer.task is not None and not consumer.task.done():
                    consumer.task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(consumer.task for consumer in consumers if consumer.task is not None),
            return_exceptions=True,
        )

    @staticmethod
    async def _stop_consumer(consumer: _Consumer) -> None:
        while True:
            try:
                consumer.queue.put_nowait(_STOP)
                break
            except queue.Full:
                await asyncio.sleep(0.001)
        if consumer.task is not None:
            await consumer.task

    def _consumers_for(self, hook: str) -> list[_Consumer]:
        registro = self._registry
        if registro is None:  # pragma: no cover - `enqueue` já filtrou
            return []
        ativos = self._consumers
        for plugin, callback in registro.entries(hook):
            chave = (hook, plugin)
            consumer = ativos.get(chave)
            if consumer is None or consumer.task is None or consumer.task.done():
                consumer = _Consumer(
                    hook=hook,
                    plugin=plugin,
                    callback=callback,
                    queue=queue.Queue(maxsize=self._queue_size),
                )
                consumer.task = asyncio.create_task(
                    self._run(consumer),
                    name=f"kairos-stream-hook:{hook}:{plugin}",
                )
                ativos[chave] = consumer
        return [
            ativos[(hook, plugin)]
            for plugin, _ in registro.entries(hook)
            if (hook, plugin) in ativos
        ]

    def _put(
        self,
        consumer: _Consumer,
        item: Any,
    ) -> bool:
        """Enfileira sem bloquear; fila cheia descarta o mais antigo e conta."""
        try:
            consumer.queue.put_nowait(item)
            return True
        except queue.Full:
            self._discard_oldest(consumer)
            try:
                consumer.queue.put_nowait(item)
                return True
            except queue.Full:  # pragma: no cover - fila só encolhe
                logger.warning(
                    "fila do hook %s do plugin %r cheia mesmo após descartar; evento perdido",
                    consumer.hook,
                    consumer.plugin,
                )
                return False

    def _discard_oldest(self, consumer: _Consumer) -> None:
        try:
            consumer.queue.get_nowait()
        except queue.Empty:  # pragma: no cover - thread já retirou o item
            return
        consumer.dropped += 1
        # Esparso de propósito: um delta por token inundaria o log, mas um
        # consumidor furado precisa aparecer no log na primeira e depois nas
        # potências de dois.
        if consumer.dropped == 1 or (consumer.dropped & (consumer.dropped - 1)) == 0:
            logger.warning(
                "hook %s do plugin %r perdeu eventos: fila cheia, descartando o mais "
                "antigo (total %d)",
                consumer.hook,
                consumer.plugin,
                consumer.dropped,
            )

    @staticmethod
    async def _run(consumer: _Consumer) -> None:
        loop = asyncio.get_running_loop()
        completed = loop.create_future()

        def complete() -> None:
            if not completed.done():
                completed.set_result(None)

        def observe() -> None:
            try:
                while not consumer.stopped.is_set():
                    try:
                        item = consumer.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    if item is _STOP or consumer.stopped.is_set():
                        return
                    try:
                        consumer.callback(**item)
                    except asyncio.CancelledError:
                        logger.warning(
                            "hook %s do plugin %r levantou CancelledError; worker continua",
                            consumer.hook,
                            consumer.plugin,
                        )
                    except BaseException as exc:  # noqa: BLE001 - falha de plugin é do plugin
                        logger.warning(
                            "hook %s do plugin %r falhou; stream preservado: %s",
                            consumer.hook,
                            consumer.plugin,
                            exc,
                        )
            finally:
                try:
                    loop.call_soon_threadsafe(complete)
                except RuntimeError:
                    if not loop.is_closed():
                        raise

        threading.Thread(
            target=observe,
            name=f"kairos-stream-hook:{consumer.hook}:{consumer.plugin}",
            daemon=True,
        ).start()
        try:
            await completed
        finally:
            consumer.stopped.set()
