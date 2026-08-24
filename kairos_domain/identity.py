"""Identidade: plataformas e origem de sessão.

Reconstruído de ``_reversa_sdd/data-dictionary.md`` §2.1-2.2 e
``_reversa_sdd/domain.md`` §1.1 (Tarefa 02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["BUILTIN_PLATFORM_COUNT", "Platform", "SessionSource"]


class Platform(StrEnum):
    """Plataformas de origem.

    **24 membros embutidos** — a spec (``data-dictionary`` §2.1) diz "23
    valores" mas lista 24; a contagem está errada, a lista está certa.
    Verificado em ``gateway/config.py:317-341``.

    Plataformas de **plugin** não precisam de membro embutido: ``_missing_``
    cria um membro dinâmico sob demanda, de modo que ``Platform("irc")``
    funciona sem alterar este enum. É a Lei 2 (cintura estreita) aplicada
    aqui — capacidade nova chega pela borda, não pelo núcleo. Os membros
    dinâmicos ficam cacheados, então a comparação por identidade continua
    estável.
    """

    LOCAL = "local"
    TELEGRAM = "telegram"
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    WHATSAPP_CLOUD = "whatsapp_cloud"
    SLACK = "slack"
    SIGNAL = "signal"
    MATTERMOST = "mattermost"
    MATRIX = "matrix"
    HOMEASSISTANT = "homeassistant"
    EMAIL = "email"
    SMS = "sms"
    DINGTALK = "dingtalk"
    API_SERVER = "api_server"
    WEBHOOK = "webhook"
    MSGRAPH_WEBHOOK = "msgraph_webhook"
    FEISHU = "feishu"
    WECOM = "wecom"
    WECOM_CALLBACK = "wecom_callback"
    WEIXIN = "weixin"
    BLUEBUBBLES = "bluebubbles"
    QQBOT = "qqbot"
    YUANBAO = "yuanbao"
    RELAY = "relay"  # adaptador de relay genérico (EXPERIMENTAL)

    @classmethod
    def _missing_(cls, value: object) -> Platform | None:
        """Cria um membro dinâmico para plataforma de plugin."""
        if not isinstance(value, str) or not value:
            return None
        member = str.__new__(cls, value)
        member._value_ = value
        member._name_ = value.upper()
        # Cacheia para que duas resoluções do mesmo nome sejam o MESMO objeto.
        cls._value2member_map_[value] = member
        return member

    @property
    def is_builtin(self) -> bool:
        """Falso para plataforma de plugin resolvida dinamicamente."""
        return self.name in _BUILTIN_PLATFORM_NAMES


@dataclass(frozen=True)
class SessionSource:
    """De onde a conversa veio.

    ``profile_route_rejected`` fica **fora da identidade** (``compare=False``):
    é resultado de uma decisão de roteamento, não uma propriedade da origem.
    Duas mensagens da mesma origem têm a mesma identidade mesmo que uma tenha
    sido rejeitada pelo roteamento de perfil e a outra não.

    ``user_id_alt`` / ``chat_id_alt`` existem porque o WhatsApp expõe duas
    identidades para o mesmo interlocutor (LID e telefone), e o sistema
    precisa reconciliá-las sem tratá-las como pessoas diferentes.
    """

    platform: Platform
    chat_id: str
    chat_name: str | None = None
    chat_type: str = "dm"
    user_id: str | None = None
    user_name: str | None = None
    thread_id: str | None = None
    chat_topic: str | None = None
    user_id_alt: str | None = None
    chat_id_alt: str | None = None
    is_bot: bool = False
    scope_id: str | None = None
    guild_id: str | None = None
    parent_chat_id: str | None = None
    message_id: str | None = None
    role_authorized: bool = False
    profile: str | None = None
    profile_route_rejected: bool = field(default=False, repr=False, compare=False)
    auto_thread_created: bool = False
    auto_thread_initial_name: str | None = None
    prospective_thread_id: str | None = None
    delivered_via_upstream_relay: bool = False

    def __post_init__(self) -> None:
        if not self.chat_id:
            raise ValueError("SessionSource exige chat_id")
        if not isinstance(self.platform, Platform):
            raise TypeError(f"platform deve ser Platform, não {type(self.platform).__name__}")


#: Congelado na importação, antes de qualquer membro dinâmico existir.
_BUILTIN_PLATFORM_NAMES = frozenset(Platform._member_map_)
BUILTIN_PLATFORM_COUNT = len(_BUILTIN_PLATFORM_NAMES)
