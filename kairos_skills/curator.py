"""O Curador — manutenção oportunista.

RF-10 a RF-15. `_reversa_sdd/skills/` (Tarefa 09).

As regras de propriedade já vivem em `kairos_domain.ownership` desde a
Tarefa 02; aqui estão as de **cadência** e **reconciliação**.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kairos_domain.ownership import (
    Actor,
    CronReferenceIndex,
    OwnershipError,
    Skill,
    SkillState,
    can_archive,
)

__all__ = [
    "Classification",
    "CuratorConfig",
    "PruneResult",
    "apply_automatic_transitions",
    "reconcile_classification",
    "should_run_now",
]


@dataclass(frozen=True)
class CuratorConfig:
    """Defaults confirmados no legado (`agent/curator.py:70-78`)."""

    enabled: bool = True
    interval_hours: int = 168  # 7 dias
    min_idle_hours: int = 2
    stale_after_days: int = 30
    archive_after_days: int = 90
    #: **A consolidação por LLM é opt-in; a poda determinística não.**
    #: A poda roda sempre que o curador está habilitado; só o passe opinativo
    #: e com custo de modelo auxiliar exige escolha explícita.
    consolidate: bool = False
    prune_builtins: bool = False
    paused: bool = False


def should_run_now(cfg: CuratorConfig, *, hours_since_last_run: float, idle_hours: float) -> bool:
    """Portões de cadência. **Best-effort**: falso não é erro.

    O `min_idle_hours` existe porque o Curador roda um fork de agente com
    custo de modelo — fazê-lo enquanto o usuário trabalha compete por recurso
    e por atenção justamente quando ambos importam.
    """
    if not cfg.enabled or cfg.paused:
        return False
    if hours_since_last_run < cfg.interval_hours:
        return False
    return idle_hours >= cfg.min_idle_hours


@dataclass
class PruneResult:
    """O que o passe mudou — e o que ele **não** mudou, e por quê.

    As protegidas são devolvidas com o motivo em vez de sumirem: uma skill
    que nunca é arquivada e ninguém sabe explicar por que vira mistério
    operacional, e o relatório do passe existe justamente para isso.
    """

    transitions: dict[str, SkillState] = field(default_factory=dict)
    protected: dict[str, str] = field(default_factory=dict)


def apply_automatic_transitions(
    skills: list[Skill],
    *,
    days_unused: dict[str, float],
    cfg: CuratorConfig,
    cron_index: CronReferenceIndex | None = None,
    actor: Actor = Actor.CURATOR,
) -> PruneResult:
    """Poda determinística por inatividade.

    `active → stale → archived`, e o arquivamento respeita as duas barreiras
    de propriedade da Tarefa 02: proveniência do usuário e referência de cron
    (inclusive de job pausado).
    """
    resultado = PruneResult()
    for skill in skills:
        dias = days_unused.get(skill.name, 0.0)

        if skill.state is SkillState.ACTIVE and dias >= cfg.stale_after_days:
            skill.state = SkillState.STALE
            resultado.transitions[skill.name] = SkillState.STALE

        if skill.state is SkillState.STALE and dias >= cfg.archive_after_days:
            try:
                can_archive(skill, actor, cron_index)
            except OwnershipError as exc:
                # A barreira de propriedade é o ponto, não uma falha: a skill
                # protegida não transiciona, o motivo é registrado, e a poda
                # das demais segue.
                resultado.protected[skill.name] = str(exc)
                continue
            skill.state = SkillState.ARCHIVED
            resultado.transitions[skill.name] = SkillState.ARCHIVED

    return resultado


@dataclass
class Classification:
    """O que aconteceu com cada skill num passe de consolidação."""

    absorbed: dict[str, str] = field(default_factory=dict)  # skill → destino
    removed: set[str] = field(default_factory=set)
    #: Divergências entre o que o modelo declarou e o que as ferramentas provam.
    discrepancies: list[str] = field(default_factory=list)


def reconcile_classification(
    declared: dict[str, str],
    tool_evidence: dict[str, str],
) -> Classification:
    """Cruza a **declaração do modelo** com a **evidência das ferramentas**.

    **O sistema não confia na palavra do modelo sobre o que ele fez.** O
    modelo declara num bloco estruturado o que absorveu; as chamadas de
    ferramenta provam o que de fato aconteceu. Quando divergem, **a evidência
    prevalece** — uma declaração é intenção, uma chamada de ferramenta é fato.

    A divergência não é descartada: vai para `discrepancies`, porque um
    modelo que declara sistematicamente o que não faz é sinal de problema no
    prompt, e apagar o sintoma esconderia isso.
    """
    resultado = Classification()

    for name, destino in tool_evidence.items():
        resultado.absorbed[name] = destino
        resultado.removed.add(name)
        declarado = declared.get(name)
        if declarado is None:
            resultado.discrepancies.append(
                f"{name}: removida pelas ferramentas, mas não declarada pelo modelo"
            )
        elif declarado != destino:
            resultado.discrepancies.append(
                f"{name}: modelo declarou absorção em {declarado!r}, ferramentas provam {destino!r}"
            )

    for name, destino in declared.items():
        if name not in tool_evidence:
            resultado.discrepancies.append(
                f"{name}: modelo declarou absorção em {destino!r}, mas nenhuma "
                "chamada de ferramenta a removeu"
            )

    return resultado
