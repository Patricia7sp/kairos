"""Servidor MCP — expõe conversas como ferramentas.

`_reversa_sdd/mcp/` §9 (Tarefa 12). Fecha **T-24**, **T-24b** e **T-24c**.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "AUTHENTICATION_RATIONALE",
    "SERVER_TOOLS",
    "UNPUBLISHED_TOOLS",
    "EventBridge",
    "server_tool_names",
]


# ---------------------------------------------------------------------------
# T-24b — sem autenticação, e o porquê fica ESCRITO
# ---------------------------------------------------------------------------

AUTHENTICATION_RATIONALE = """\
O servidor MCP é **stdio puro** e não tem autenticação, por desenho.

A fronteira é o sistema operacional: quem consegue spawnar `kairos mcp serve`
já roda como o usuário e já tem acesso a `~/.kairos`. Um token aqui protegeria
contra nada — o atacante que pode iniciar o processo pode ler o token.

**Isto vale ENQUANTO o transporte for stdio.** Se algum dia houver transporte
remoto, a decisão precisa ser revista ANTES de o transporte existir, não
depois: um servidor remoto sem autenticação é superfície de acesso à
mensageria do usuário.
"""


# ---------------------------------------------------------------------------
# T-24 — a superfície de aprovação NÃO é publicada
# ---------------------------------------------------------------------------

#: Ferramentas deliberadamente **não publicadas**, com o motivo.
UNPUBLISHED_TOOLS: dict[str, str] = {
    "permissions_list_open": (
        "No legado, `_pending_approvals` nunca é populado: esta ferramenta "
        "sempre devolveria lista vazia."
    ),
    "permissions_respond": (
        "No legado, devolveria {'resolved': true} SEM EFEITO — não há IPC com "
        "o gateway que consuma a resposta. Uma ferramenta que reporta sucesso "
        "sem efeito é pior que uma ferramenta ausente: o cliente acredita ter "
        "aprovado, e nada aprovou. Publicar exige a IPC primeiro, e ela "
        "pertence ao gateway."
    ),
    "attachments_list": (
        "No Kairos nada persiste anexo: o inbound não armazena mídia e "
        "`messages` guarda só conteúdo textual. Publicar seria reportar "
        "sucesso sem efeito (sempre `0 anexos`). D-MCP.11."
    ),
}

#: O que o servidor de fato expõe.
SERVER_TOOLS: tuple[str, ...] = (
    "conversations_list",
    "conversation_read",
    "messages_send",
    "events_poll",
    "platforms_list",
    "session_info",
    "conversation_search",
)


def server_tool_names() -> tuple[str, ...]:
    """As ferramentas publicadas. As não publicadas nunca entram aqui."""
    return SERVER_TOOLS


# ---------------------------------------------------------------------------
# T-24c — baseline no startup, sem reproduzir histórico
# ---------------------------------------------------------------------------


@dataclass
class EventBridge:
    """Ponte de eventos com baseline no start.

    **O problema (#13414):** um bridge que sobe sobre um banco existente e
    começa a emitir tudo que encontra despeja meses de histórico no cliente
    MCP como se fosse novidade.

    **A armadilha da correção óbvia:** baselinar por "marca de tempo de
    início" perde a primeira mensagem de uma conversa **nova**, porque a
    conversa não existia no start e portanto não tem baseline. É o caso que
    mais importa — a conversa nova é justamente a que o cliente quer ver.

    A solução: baseline **por sessão**, e sessão desconhecida entrega desde a
    primeira mensagem.
    """

    #: sessão → maior id já visto no momento do baseline.
    _baseline: dict[str, int] = field(default_factory=dict)
    _started: bool = False

    def baseline_existing(self, sessions: dict[str, int]) -> None:
        """Marca o que já existia. Chamado uma vez, no start."""
        self._baseline = dict(sessions)
        self._started = True

    def should_emit(self, session_id: str, message_id: int) -> bool:
        if not self._started:
            return True
        if session_id not in self._baseline:
            # Sessão NOVA: não existia no baseline, então tudo nela é novidade
            # — inclusive a primeira mensagem.
            return True
        return message_id > self._baseline[session_id]

    def note_emitted(self, session_id: str, message_id: int) -> None:
        atual = self._baseline.get(session_id)
        if atual is None or message_id > atual:
            self._baseline[session_id] = message_id
