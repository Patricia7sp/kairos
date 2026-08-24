"""Aprovação de edição no ACP — com o workspace como PISO.

`_reversa_sdd/acp-adapter/` (Tarefa 17). Materializa **T-15** (decisão de
`questions.md#pergunta-11`, fecha G-16) e **T-16** (pergunta 12, fecha G-22).
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

__all__ = [
    "SENSITIVE_NAMES",
    "SENSITIVE_PATH_PARTS",
    "AutoApprovePolicy",
    "EditProposal",
    "PermissionOption",
    "build_permission_options",
    "edit_approval_requester",
    "map_outcome",
    "should_auto_approve_edit",
]


class AutoApprovePolicy(StrEnum):
    ASK = "ask"
    WORKSPACE_SESSION = "workspace_session"
    #: 🔀 DIVERGÊNCIA: no legado significava "qualquer caminho por esta
    #: sessão". No Kairos significa "qualquer caminho **dentro do workspace**
    #: por esta sessão" — ver `should_auto_approve_edit`.
    SESSION = "session"


#: Sempre perguntam, **dentro e fora** do workspace, sob qualquer política.
SENSITIVE_NAMES: frozenset[str] = frozenset(
    {
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
    }
)
SENSITIVE_PATH_PARTS: frozenset[str] = frozenset({".git", ".ssh"})


@dataclass(frozen=True)
class EditProposal:
    """Congelada: é o material do diff mostrado no editor.

    Uma proposta mutável entre a exibição e a aprovação permitiria mostrar um
    diff e aplicar outro.
    """

    tool_name: str
    path: str
    new_text: str
    old_text: str | None = None
    arguments: dict = field(default_factory=dict)


def _is_sensitive(path: Path) -> bool:
    partes = {p.lower() for p in path.parts}
    if partes & SENSITIVE_PATH_PARTS:
        return True
    return path.name.lower() in SENSITIVE_NAMES


def _within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def should_auto_approve_edit(
    proposal: EditProposal,
    policy: AutoApprovePolicy,
    *,
    cwd: str | None = None,
    temp_is_floor: bool = False,
) -> bool:
    """**O workspace é PISO, não alargador** (T-15).

    No legado o `cwd` só **ampliava** a auto-aprovação sob a política
    `workspace_session`; caminho externo não era bloqueado, apenas voltava a
    perguntar — e sob `session` **qualquer** caminho passava, com cinco nomes
    de arquivo entre a política e o disco inteiro do usuário.

    Aqui nenhuma política auto-aprova fora do workspace. Não é contenção
    dura — o caminho externo **volta ao diálogo**, não é recusado —, porque
    recusar quebraria monorepo com irmãos, edição em `~/.config` e
    movimentação entre projetos, e o escape hatch necessário reabriria o
    buraco.

    `temp_is_floor` torna o diretório temporário um piso declarado ao lado do
    workspace. **Default falso**, deliberadamente: no legado o `/tmp` passava
    como efeito colateral de `resolve()` seguir symlink no macOS, não como
    intenção.
    """
    if policy is AutoApprovePolicy.ASK:
        return False

    path = Path(proposal.path).expanduser().resolve(strict=False)

    # A lista sensível vale dos dois lados da fronteira.
    if _is_sensitive(path):
        return False

    # O piso: sem workspace resolvido, não há dentro — então não auto-aprova.
    if not cwd:
        return False

    root = Path(cwd).expanduser().resolve(strict=False)
    if _within(path, root):
        return True

    if temp_is_floor:
        tmp = Path(tempfile.gettempdir()).resolve(strict=False)
        if _within(path, tmp):
            return True

    return False


# ---------------------------------------------------------------------------
# T-16 — deny_always → never
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PermissionOption:
    option_id: str
    kind: str
    name: str


#: 🔀 DIVERGÊNCIA (T-16): no legado `deny_always` colapsava em `deny`, porque
#: a ponte ACP não escrevia em `approvals.deny`. Aqui mapeia para `never`, que
#: **persiste** — o `approvals.deny` já existia; faltava a ligação.
OPTION_ID_TO_KAIROS: dict[str, str] = {
    "allow_once": "once",
    "allow_session": "session",
    "allow_always": "always",
    "deny": "deny",
    "deny_always": "never",
}


def build_permission_options(
    *,
    allow_permanent: bool,
    smart_denied: bool = False,
    supports_reject_always: bool = True,
) -> list[PermissionOption]:
    """Monta as opções mostradas ao editor.

    Sob `smart_denied` a lista encolhe para permitir-uma-vez e negar: não se
    oferece "negar sempre" para algo que o sistema **já** nega por política —
    a opção sugeriria que o usuário está decidindo algo que já foi decidido.
    """
    opcoes = [PermissionOption("allow_once", "allow_once", "Permitir uma vez")]
    if not smart_denied:
        opcoes.append(
            # O ACP não tem kind com escopo de sessão: usa-se o persistente
            # mais próximo, mantendo a semântica do Kairos no option_id.
            PermissionOption("allow_session", "allow_always", "Permitir nesta sessão")
        )
        if allow_permanent:
            opcoes.append(PermissionOption("allow_always", "allow_always", "Permitir sempre"))
    opcoes.append(PermissionOption("deny", "reject_once", "Negar"))
    if not smart_denied and supports_reject_always:
        opcoes.append(PermissionOption("deny_always", "reject_always", "Nunca permitir"))
    return opcoes


def map_outcome(option_id: str) -> str:
    """Traduz a escolha do editor. **Simetria exata** entre allow e deny."""
    try:
        return OPTION_ID_TO_KAIROS[option_id]
    except KeyError:
        # Opção desconhecida vinda do editor: nega. Fail-closed é a única
        # leitura segura de uma resposta que não se entende.
        return "deny"


# ---------------------------------------------------------------------------
# O guard, ligado por ContextVar
# ---------------------------------------------------------------------------

_REQUESTER: ContextVar = ContextVar("KAIROS_ACP_EDIT_APPROVAL_REQUESTER", default=None)


@contextmanager
def edit_approval_requester(requester):
    """Liga o guard pela duração de uma execução ACP.

    **Só o ACP o liga.** CLI, gateway e cron o deixam desligado e portanto
    **passam por fora** — o que torna a aprovação uma política *do editor*, e
    não do registry de ferramentas. A distinção importa ao auditar: para
    aquelas superfícies a guarda é **inexistente**, não permissiva.
    """
    token = _REQUESTER.set(requester)
    try:
        yield
    finally:
        _REQUESTER.reset(token)


def require_edit_approval(proposal: EditProposal) -> str | None:
    """Devolve mensagem de bloqueio, ou `None` para seguir.

    **Fail-closed**: exceção no requester conta como negação. Um aprovador que
    quebra não pode virar um aprovador que aceita.
    """
    requester = _REQUESTER.get()
    if requester is None:
        return None  # a guarda não está ligada nesta superfície

    try:
        aprovado = bool(requester(proposal))
    except Exception:  # noqa: BLE001
        # DELIBERADO (invariante 14): qualquer falha do aprovador nega.
        return f"Edit approval denied: o aprovador falhou ao avaliar {proposal.path!r}"

    if aprovado:
        return None
    return f"Edit approval denied: {proposal.path!r}"
