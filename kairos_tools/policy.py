"""Listas de política de segurança — imutáveis em runtime.

`_reversa_sdd/tools/` (Tarefa 08).

Ambas são `frozenset`, e isso é **decisão deliberada, não detalhe de tipo**:
uma política de segurança que pode ser mutada em runtime não é política, é
sugestão. Um plugin mal-intencionado — ou apenas descuidado — que consiga
acrescentar um nome à allowlist de sandbox anula a fronteira inteira.
"""

from __future__ import annotations

from types import MappingProxyType

__all__ = [
    "DELEGATE_BLOCKED_TOOLS",
    "DELEGATE_BLOCK_REASONS",
    "SANDBOX_ALLOWED_TOOLS",
    "delegate_block_reason",
    "sandbox_allows",
]

#: Sandbox opera por **allowlist**, não por blocklist. A diferença importa:
#: uma blocklist esquece o que ainda não existe, e toda ferramenta nova nasce
#: permitida. Aqui, toda ferramenta nova nasce invisível no sandbox até que
#: alguém decida o contrário.
SANDBOX_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "web_search",
        "web_extract",
        "read_file",
        "write_file",
        "search_files",
        "patch",
        "terminal",
    }
)

#: Delegação opera por **blocklist**, e aqui é o certo: o subagente é o mesmo
#: agente com menos autoridade, então herdar tudo menos exceções nomeadas é o
#: default correto. Cada exceção traz o motivo, que é o que impede a lista de
#: virar folclore.
DELEGATE_BLOCK_REASONS: MappingProxyType[str, str] = MappingProxyType(
    {
        "delegate_task": "sem delegação recursiva — um subagente que delega vira árvore sem fundo",
        "clarify": "sem interação com o usuário — o subagente não tem a quem perguntar",
        "memory": "sem escrita no MEMORY.md compartilhado — o pai é o dono daquele arquivo",
        "send_message": "sem efeito colateral entre plataformas em nome do pai",
        "cronjob": "sem agendar mais trabalho no nome do pai",
    }
)

DELEGATE_BLOCKED_TOOLS: frozenset[str] = frozenset(DELEGATE_BLOCK_REASONS)


def sandbox_allows(tool_name: str) -> bool:
    return tool_name in SANDBOX_ALLOWED_TOOLS


def delegate_block_reason(tool_name: str) -> str | None:
    """O motivo do bloqueio, ou `None` se a ferramenta é permitida.

    Devolver o motivo em vez de um booleano é deliberado: a mensagem de
    bloqueio precisa dizer *por que*, senão o modelo tenta de novo por outro
    caminho achando que foi acidente.
    """
    return DELEGATE_BLOCK_REASONS.get(tool_name)
