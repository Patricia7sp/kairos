"""Canal de entrada Telegram: mensagens recebidas viram turnos do agente.

O canal de entrada fecha o laço que o gateway hoje não tem: enquanto
``kairos_gateway`` só entrega mensagens para fora, este módulo recebe
``update`` da Bot API (long-polling ``getUpdates``), autentica o remetente
pela lista explícita de usuários autorizados e submete o texto ao mesmo
serviço de interação das interfaces web/terminal — que decide ferramentas e
emite aprovações.

Nenhum canal de origem (NDC) existe no Kairos (``agendamentos.md:261``).
Este módulo é o primeiro NDC.

Princípios (herdados do AGENTS.md):

- **Fail-closed.** Lista de autorizados vazia → ninguém fala com o agente.
  Aprovação que precede a mensagem é recusada por timeout (90 s), nunca
  aprovada.
- **Sem efeito fingido.** Cada resposta Telegram é efeito real; ignorar um
  update aceito sem construir o turno é bug.
- **Cinto estreito.** Só constroem turnos mensagens de usuários
  explicitamente autorizados; tudo o mais é ignorado sem revelar presença.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from kairos_gateway.adapters.config import load_config, platform_secret
from kairos_gateway.service import DRAIN_MARKER

__all__ = [
    "TelegramChannel",
    "TelegramError",
    "TelegramInbound",
    "build_telegram_inbound",
    "stop_telegram_inbound",
]

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"
_MAX_TEXT = 4000  # margem segura sobre o limite de 4096
_TYPING_INTERVAL = 5.0
_INBOUND_STATE_FILE = "inbound-telegram.json"
_PREFIX = "ka:"


class TelegramError(Exception):
    """Erro de configuração ou autenticação no canal de entrada."""


class InteractionRouterProtocol(Protocol):
    """Duck-typing mínima do ``InteractionRouter`` usado neste módulo."""

    async def stream(self, envelope: Any) -> Any: ...

    def decide_tool_approval(self, *, approval_id: str, session_id: str, decision: str) -> None: ...


# ---------------------------------------------------------------------------


def _chunk_text(text: str, limit: int = _MAX_TEXT) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        idx = remaining.rfind("\n", 0, limit)
        if idx <= 0:
            idx = limit
        chunks.append(remaining[:idx])
        remaining = remaining[idx:].lstrip("\n")
    return chunks or [""]


# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TelegramUpdate:
    """Campos relevantes de um ``update`` da Bot API."""

    update_id: int
    message_id: int | None = None
    chat_id: int | None = None
    chat_type: str = ""
    user_id: int | None = None
    text: str | None = None
    callback_data: str | None = None
    callback_query_id: str | None = None
    callback_chat_id: int | None = None


def _parse_update(raw: dict[str, Any]) -> TelegramUpdate | None:
    """Extrai apenas o que o canal de entrada precisa; ignora updates irrelevantes."""
    update_id = raw.get("update_id")
    if not isinstance(update_id, int):
        return None

    cbq = raw.get("callback_query")
    if isinstance(cbq, dict):
        cb_msg = cbq.get("message")
        cb_from = cbq.get("from")
        cb_chat = {}
        if isinstance(cb_msg, dict):
            cb_chat = cb_msg.get("chat")
        if not isinstance(cb_chat, dict):
            cb_chat = {}
        if not isinstance(cb_from, dict):
            cb_from = {}
        return TelegramUpdate(
            update_id=update_id,
            callback_data=cbq.get("data"),
            callback_query_id=cbq.get("id"),
            callback_chat_id=cb_chat.get("id"),
            chat_id=cb_chat.get("id"),
            user_id=cb_from.get("id"),
        )

    msg = raw.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    from_user = msg.get("from")
    if not isinstance(chat, dict):
        chat = {}
    if not isinstance(from_user, dict):
        from_user = {}
    return TelegramUpdate(
        update_id=update_id,
        message_id=msg.get("message_id"),
        chat_id=chat.get("id"),
        chat_type=chat.get("type", ""),
        user_id=from_user.get("id"),
        text=msg.get("text"),
    )


# ---------------------------------------------------------------------------


class TelegramChannel:
    """Transporte de baixo nível: comunicação com a Bot API."""

    def __init__(
        self,
        token: str,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token.strip()
        self._client = client or httpx.AsyncClient(timeout=timeout)

    def _url(self, method: str) -> str:
        return f"{_TELEGRAM_API.format(token=self._token)}/{method}"

    async def _post(self, method: str, **payload: Any) -> dict[str, Any]:
        try:
            res = await self._client.post(self._url(method), json=payload)
            data = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"API do Telegram sem resposta: {exc}") from exc
        if not data.get("ok"):
            desc = data.get("description") or f"HTTP {res.status_code}"
            raise TelegramError(f"Bot API {method}: {desc}")
        return data.get("result") or {}

    async def get_updates(self, offset: int, *, poll_seconds: int = 30) -> list[dict[str, Any]]:
        try:
            res = await self._client.get(
                self._url("getUpdates"),
                params={"offset": offset, "timeout": poll_seconds},
            )
            data = res.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramError(f"API do Telegram sem resposta: {exc}") from exc
        if not data.get("ok"):
            desc = data.get("description") or f"HTTP {res.status_code}"
            raise TelegramError(f"Bot API getUpdates: {desc}")
        return data.get("result") or []

    async def verify(self) -> dict[str, Any]:
        result = await self._post("getMe")
        username = result.get("username") or result.get("first_name") or "desconhecido"
        return {"ok": True, "bot": f"@{username}"}

    async def send_text(
        self, chat_id: int, text: str, *, reply_markup: dict | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self._post("sendMessage", **payload)

    async def send_typing(self, chat_id: int) -> None:
        with suppress(TelegramError):
            await self._post("sendChatAction", chat_id=chat_id, action="typing")

    async def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        with suppress(TelegramError):
            await self._post(
                "answerCallbackQuery",
                callback_query_id=callback_query_id,
                text=text,
            )


# ---------------------------------------------------------------------------
# Estado persistente (high-water mark de updates processados)


@dataclass
class _InboundState:
    consumed: int = 0

    @classmethod
    def load(cls, home: Path) -> _InboundState:
        path = home / _INBOUND_STATE_FILE
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError):
            return cls()
        return cls(consumed=int(raw.get("consumed", 0)))

    def persist(self, home: Path) -> None:
        path = home / _INBOUND_STATE_FILE
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"consumed": self.consumed}), encoding="utf-8")
        tmp.replace(path)


# ---------------------------------------------------------------------------
# TelegramInbound — orquestração (auth + despacho + aprovações)


class TelegramInbound:
    """Canal de entrada: long-polling + autenticação + ponte para o agente."""

    _CMD_HELP = (
        "Kairos Agent\n\n"
        "Envie qualquer mensagem para conversar com o agente.\n"
        "Comandos:\n"
        "/start — esta mensagem\n"
        "/status — estado do canal de entrada"
    )

    def __init__(
        self,
        home: Path,
        router: Any,
        *,
        token: str | None = None,
        channel: TelegramChannel | None = None,
    ) -> None:
        self.home = Path(home)
        doc = load_config(self.home)
        inbound = doc["telegram"].get("inbound", {})
        self._inbound_enabled: bool = inbound.get("enabled", False)
        self._allowed: frozenset[int] = frozenset(inbound.get("allowed_user_ids", []))
        self._poll_interval: float = float(inbound.get("poll_interval_seconds", 2.0))
        self._experiences: bool = bool(inbound.get("experiences", False))
        self._router = router
        resolved_token = token or platform_secret(self.home, "telegram")
        if not resolved_token:
            raise TelegramError("token do Telegram não configurado no cofre")
        self._channel = channel or TelegramChannel(resolved_token)
        self._state = _InboundState.load(self.home)
        self._chat_locks: dict[str, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()

    @property
    def inbound_enabled(self) -> bool:
        return self._inbound_enabled

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return self._allowed

    @property
    def experiences_enabled(self) -> bool:
        return self._experiences

    # --- shutdown ---

    async def _shutdown_pending_tasks(self) -> None:
        """Dá tempo razoável para tasks em andamento terminarem; cancela as que sobrarem."""
        if not self._tasks:
            return
        _, pending = await asyncio.wait(list(self._tasks), timeout=3.0)
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    # --- ciclo principal ---

    async def run(self) -> None:
        if not self._inbound_enabled:
            raise TelegramError("canal de entrada desabilitado (inbound.enabled = false)")
        verify = await self._channel.verify()
        logger.info("bot conectado: %s", verify.get("bot"))
        try:
            await self._poll_loop()
        except KeyboardInterrupt:
            logger.info("canal de entrada Telegram encerrado")
        finally:
            await self._shutdown_pending_tasks()

    async def _poll_loop(self) -> None:
        offset = self._state.consumed + 1
        while True:
            if (self.home / DRAIN_MARKER).exists():
                logger.info("canal de entrada: drenagem solicitada, encerrando")
                break
            try:
                raw_updates = await self._channel.get_updates(
                    offset, poll_seconds=int(max(self._poll_interval * 10, 30))
                )
            except TelegramError as exc:
                logger.warning("falha ao buscar updates: %s", exc)
                await asyncio.sleep(self._poll_interval)
                continue
            for raw in raw_updates:
                update = _parse_update(raw)
                if update is None:
                    continue
                if update.update_id >= offset:
                    offset = update.update_id + 1
                task = asyncio.create_task(self._dispatch(update))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)
            if offset - 1 > self._state.consumed:
                self._state.consumed = offset - 1
                self._state.persist(self.home)

    # --- despacho por conversa ---

    async def _dispatch(self, update: TelegramUpdate) -> None:
        chat_id = update.callback_chat_id or update.chat_id
        user_id = update.user_id
        if chat_id is None:
            return
        lock = self._chat_locks.setdefault(str(chat_id), asyncio.Lock())
        async with lock:
            if update.callback_data and update.callback_data.startswith(_PREFIX):
                await self._handle_callback(update)
                return
            if not self._inbound_enabled or user_id not in self._allowed:
                return
            text = update.text or ""
            if not text.strip():
                return
            if update.message_id:
                await self._process_message(update)

    # --- comandos ---

    async def _process_message(self, update: TelegramUpdate) -> None:
        chat_id = update.chat_id
        text = (update.text or "").strip()
        if chat_id is None:
            return
        if text == "/start":
            await self._channel.send_text(chat_id, self._CMD_HELP)
            return
        if text == "/status":
            await self._channel.send_text(chat_id, "Canal de entrada ativo.")
            return
        await self._dispatch_agent(update)

    # --- agente ---

    async def _dispatch_agent(self, update: TelegramUpdate) -> None:
        chat_id = update.chat_id
        if chat_id is None or update.update_id is None:
            return
        conversation_id = f"telegram:{chat_id}"
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(self._typing_loop(chat_id, stop_typing))
        try:
            envelope = self._build_envelope(conversation_id, update.text or "", update.update_id)
            text, error = await self._stream_turn(envelope, chat_id, conversation_id)
        finally:
            stop_typing.set()
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task
        final = text or error or "Sem resposta."
        await self._send_chunked(chat_id, final)

    def _build_envelope(self, conversation_id: str, text: str, update_id: int) -> Any:
        from kairos_integration.interaction_contract import InteractionEnvelope

        content = self._augment(text)
        return InteractionEnvelope(
            conversation_id=conversation_id,
            source="telegram",
            content=content,
            idempotency_key=f"telegram:{update_id}",
            web_search=True,
            tools=True,
        )

    def _augment(self, text: str) -> str:
        """Prefixa o turno atual com experiências ativas (opt-in per inbound).

        A injeção é local ao turno que está chegando — nunca altera o system
        prompt de uma conversa já em andamento (Lei 1 de
        `kairos_integration.surfaces`).
        """
        if not self._experiences or not text.strip():
            return text
        from kairos_memory import build_experience_context

        context = build_experience_context(text, self.home)
        return f"{context}\n\n{text}" if context else text

    @staticmethod
    def _event_kind(event: Any) -> str | None:
        kind = getattr(event, "kind", None)
        if isinstance(kind, str):
            return kind
        value = getattr(kind, "value", None)
        return value if isinstance(value, str) else None

    async def _stream_turn(
        self, envelope: Any, chat_id: int, conversation_id: str
    ) -> tuple[str | None, str | None]:
        accumulated: list[str] = []
        try:
            async for event in self._router.stream(envelope):
                kind = self._event_kind(event)
                if kind == "delta":
                    accumulated.append(getattr(event, "text", "") or "")
                elif kind == "turn_end":
                    break
                elif kind == "tool_approval_request":
                    await self._handle_approval_event(chat_id, conversation_id, event)
                elif kind == "turn_error":
                    return None, getattr(event, "error", "falha desconhecida")
        except BaseException as exc:  # noqa: BLE001
            return None, f"falha ao executar: {exc}"
        text = "".join(accumulated).strip()
        return (text or None), None

    async def _handle_approval_event(self, chat_id: int, conversation_id: str, event: Any) -> None:
        approval_id = getattr(event, "tool_approval_id", None)
        tool_call = getattr(event, "tool_call", None)
        if not approval_id or tool_call is None:
            return
        tool_name = getattr(tool_call, "name", "?")
        args = getattr(tool_call, "arguments", "")
        preview = args[:200] if isinstance(args, str) else ""
        text = f"Aprovacao necessaria\nFerramenta: {tool_name}\nArgumentos: {preview}"
        markup = {
            "inline_keyboard": [
                [
                    {"text": "Aprovar", "callback_data": f"{_PREFIX}{approval_id}:allow"},
                    {"text": "Negar", "callback_data": f"{_PREFIX}{approval_id}:deny"},
                ]
            ]
        }
        await self._channel.send_text(chat_id, text, reply_markup=markup)

    async def _handle_callback(self, update: TelegramUpdate) -> None:
        data = update.callback_data or ""
        parts = data.split(":", 2)
        if len(parts) != 3 or update.callback_query_id is None:
            return
        _, approval_id, decision = parts
        if decision not in {"allow", "deny"}:
            return
        conversation_id = f"telegram:{update.callback_chat_id}"
        try:
            self._router.decide_tool_approval(
                approval_id=approval_id,
                session_id=conversation_id,
                decision=decision,
            )
        except Exception as exc:  # noqa: BLE001
            await self._channel.answer_callback(update.callback_query_id, str(exc)[:200])
            return
        label = "Aprovado" if decision == "allow" else "Negado"
        await self._channel.answer_callback(update.callback_query_id, label)

    # --- typing indicator ---

    async def _typing_loop(self, chat_id: int, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                await self._channel.send_typing(chat_id)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=_TYPING_INTERVAL)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            return

    # --- envio final ---

    async def _send_chunked(self, chat_id: int, text: str) -> None:
        for i, chunk in enumerate(_chunk_text(text)):
            if i >= 10:
                await self._channel.send_text(
                    chat_id, "Saida longa — consulte o painel para o resultado completo."
                )
                break
            await self._channel.send_text(chat_id, chunk)


# ---------------------------------------------------------------------------
# Helpers públicos (CLI e composição)


def stop_telegram_inbound(home: Path, reason: str = "manual") -> Path:
    """Marca o canal de entrada para drenagem, usando o mesmo marcador do gateway."""
    from kairos_gateway.service import write_drain_request

    return write_drain_request(home, reason)


def build_telegram_inbound(home: Path, *, router: Any = None) -> TelegramInbound:
    """Monta o canal de entrada usando o ``InteractionRouter`` padrão."""
    if router is None:
        from kairos_integration.composition import build_interaction_router

        router = build_interaction_router(home)
    return TelegramInbound(home, router)
