"""Propriedade e autonomia — quem pode mexer no quê.

``_reversa_sdd/domain.md`` §2.3. É o grupo de regras que protege o trabalho
do usuário de atores autônomos, e o marcado 🟢🟢 na análise.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from kairos_domain.message import DomainRuleViolation

__all__ = [
    "Provenance",
    "SkillState",
    "Skill",
    "Actor",
    "OwnershipError",
    "HardDeleteByAutonomousActor",
    "UserSkillAutoCurated",
    "ProtectedByCronReference",
    "can_archive",
    "assert_can_hard_delete",
]


class Provenance(str, Enum):
    """De quem a skill é.

    A distinção não é cosmética: ela decide quem pode arquivá-la.
    """

    USER = "user"          # o usuário pediu — pertence ao usuário
    SEDIMENT = "sediment"  # sedimento do fork de revisão — território do Curador


class SkillState(str, Enum):
    ACTIVE = "active"
    STALE = "stale"
    ARCHIVED = "archived"


class Actor(str, Enum):
    USER_FOREGROUND = "user_foreground"
    CURATOR = "curator"
    BACKGROUND_REVIEW = "background_review"

    @property
    def is_autonomous(self) -> bool:
        return self is not Actor.USER_FOREGROUND


class OwnershipError(DomainRuleViolation):
    """Violação de uma regra de propriedade."""


class HardDeleteByAutonomousActor(OwnershipError):
    """Invariante 9 — atores autônomos nunca fazem hard-delete."""


class UserSkillAutoCurated(OwnershipError):
    """Invariante 10 — skill do usuário nunca é auto-curada."""


class ProtectedByCronReference(OwnershipError):
    """Skill referenciada por job de cron é protegida do arquivamento."""


@dataclass
class Skill:
    name: str
    provenance: Provenance
    state: SkillState = SkillState.ACTIVE


@dataclass
class CronReferenceIndex:
    """Quais skills algum job de cron referencia.

    Inclui jobs **pausados e desabilitados** — deliberadamente. Um job
    desabilitado hoje é reabilitado amanhã, e encontrar a skill que ele
    precisava já arquivada é uma falha silenciosa que só aparece na próxima
    execução agendada.
    """

    referenced: set[str] = field(default_factory=set)

    def references(self, skill_name: str) -> bool:
        return skill_name in self.referenced


def can_archive(
    skill: Skill,
    actor: Actor,
    cron_index: CronReferenceIndex | None = None,
) -> None:
    """Levanta se este ator não pode arquivar esta skill.

    Duas barreiras, nesta ordem:

    1. **Proveniência** — o Curador só consolida ou poda o que ele mesmo
       criou. Skill que o usuário pediu pertence ao usuário.
    2. **Referência de cron** — mesmo sendo sedimento, se algum job a
       referencia (inclusive pausado ou desabilitado), fica protegida.
    """
    if actor.is_autonomous and skill.provenance is Provenance.USER:
        raise UserSkillAutoCurated(
            f"{actor.value} não pode curar a skill {skill.name!r}: "
            "proveniência é do usuário"
        )

    if cron_index is not None and cron_index.references(skill.name):
        raise ProtectedByCronReference(
            f"skill {skill.name!r} é referenciada por um job de cron "
            "(inclusive pausado ou desabilitado) e não pode ser arquivada"
        )


def assert_can_hard_delete(actor: Actor) -> None:
    """Invariante 9 — só o usuário em foreground apaga de verdade.

    Atores autônomos arquivam; tudo é ledgerado e recuperável. A assimetria
    é deliberada: um engano do usuário é reversível pelo ledger, um engano
    de um ator autônomo em loop não seria percebido a tempo.
    """
    if actor.is_autonomous:
        raise HardDeleteByAutonomousActor(
            f"{actor.value} é autônomo e nunca faz hard-delete; use arquivamento"
        )
