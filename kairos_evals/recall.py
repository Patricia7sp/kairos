"""Harness de recall de compactação — determinístico, com juiz LLM opcional.

`_reversa_sdd/evals/` e ADR 014 (Tarefa 21). Fecha **G-19**.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "DEFAULT_RECALL_THRESHOLD",
    "Fact",
    "FactKind",
    "RecallReport",
    "RecallResult",
    "check_gate",
    "score_recall",
]


class FactKind(StrEnum):
    """O que se espera que sobreviva à compactação.

    A separação importa porque o critério de acerto é diferente: um SHA ou um
    caminho tem de sobreviver **literalmente**, enquanto uma decisão sobrevive
    em substância. Medir os dois com a mesma régua esconde a falha que mais
    dói — a compactação que "resume bem" e perde o número.
    """

    IDENTIFIER = "identifier"  # SHA, id, caminho, código de erro
    DECISION = "decision"  # o que foi decidido e por quê
    CONSTRAINT = "constraint"  # restrição que ainda vale
    OUTCOME = "outcome"  # resultado de uma ação


@dataclass(frozen=True)
class Fact:
    kind: FactKind
    text: str
    #: Para `IDENTIFIER`: a forma exata que precisa aparecer.
    literal: str | None = None

    @property
    def must_survive_literally(self) -> bool:
        return self.kind is FactKind.IDENTIFIER


@dataclass
class RecallResult:
    fact: Fact
    recalled: bool
    method: str


#: Abaixo disso o gate reprova.
DEFAULT_RECALL_THRESHOLD = 0.85


def score_recall(
    facts: list[Fact],
    summary: str,
    *,
    judge=None,
) -> list[RecallResult]:
    """Mede quantos fatos sobreviveram.

    **Identificadores são checados mecanicamente**, nunca pelo juiz: a
    verificação de que um SHA aparece é determinística, e delegá-la a um
    modelo introduziria ruído numa medida que não precisa dele. É a mesma
    regra da compactação (regra 10): identificadores são indexados
    mecanicamente, não confiados ao sumarizador.

    O juiz LLM — opcional — cobre só o que exige julgamento: decisões,
    restrições e resultados.
    """
    resultados: list[RecallResult] = []
    for fact in facts:
        if fact.must_survive_literally:
            alvo = fact.literal or fact.text
            resultados.append(RecallResult(fact, alvo in summary, method="literal"))
            continue

        if judge is None:
            # Sem juiz, cai numa heurística conservadora: substring da forma
            # normalizada. Conservadora de propósito — é melhor o gate
            # reprovar por medida grosseira que aprovar por ausência de medida.
            resultados.append(
                RecallResult(fact, _loose_contains(summary, fact.text), method="heurística")
            )
            continue

        try:
            veredito = bool(judge(fact.text, summary))
        except Exception:  # noqa: BLE001
            # DELIBERADO: juiz indisponível não pode APROVAR por omissão. Um
            # eval que passa porque o juiz caiu é pior que eval nenhum.
            veredito = False
        resultados.append(RecallResult(fact, veredito, method="juiz"))

    return resultados


#: Fração das palavras de prosa que precisa sobreviver. Baixa de propósito:
#: prosa parafraseia, e exigir muito faria a heurística reprovar resumo bom.
_PROSE_RATIO = 0.5


def _identity_tokens(words: list[str]) -> list[str]:
    """Tokens que carregam **identidade**: números e siglas.

    A primeira versão desta heurística filtrava por comprimento (>3 chars) e
    descartava exatamente estes — de modo que "item 3" e "item 7" ficavam
    indistinguíveis, e o harness reportava **100% de recall com metade dos
    fatos perdidos**. Falso positivo num gate é pior que gate nenhum: aprova
    a compactação quebrada com um número tranquilizador.
    """
    return [w for w in words if any(c.isdigit() for c in w) or (w.isupper() and len(w) > 1)]


def _loose_contains(haystack: str, needle: str) -> bool:
    """Heurística sem juiz. **Enviesada para reprovar**, e por partes.

    Identidade e prosa são medidas separadamente porque falham de formas
    diferentes: um número que some **é** fato perdido; uma palavra de prosa
    pode ter sido parafraseada.
    """
    bruto = needle.split()
    agulha = " ".join(needle.lower().split())
    palheiro = " ".join(haystack.lower().split())
    if agulha in palheiro:
        return True

    # Identidade é OBRIGATÓRIA: qualquer uma ausente reprova.
    for token in _identity_tokens(bruto):
        if token.lower() not in palheiro:
            return False

    prosa = [w for w in agulha.split() if len(w) > 3 and not any(c.isdigit() for c in w)]
    if not prosa:
        # Só havia identidade, e ela sobreviveu.
        return bool(_identity_tokens(bruto))
    presentes = sum(1 for w in prosa if w in palheiro)
    return presentes / len(prosa) >= _PROSE_RATIO


@dataclass
class RecallReport:
    results: list[RecallResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def recalled(self) -> int:
        return sum(1 for r in self.results if r.recalled)

    @property
    def score(self) -> float:
        return 1.0 if self.total == 0 else self.recalled / self.total

    @property
    def lost_identifiers(self) -> list[Fact]:
        """Identificadores perdidos são reportados **à parte**.

        Um score de 0,9 com um SHA perdido é pior que 0,8 sem nenhum: o
        agregado esconde exatamente a falha mais cara.
        """
        return [r.fact for r in self.results if not r.recalled and r.fact.must_survive_literally]


@dataclass(frozen=True)
class GateResult:
    passed: bool
    score: float
    threshold: float
    reason: str


def check_gate(
    report: RecallReport,
    *,
    threshold: float = DEFAULT_RECALL_THRESHOLD,
    fail_on_lost_identifier: bool = True,
) -> GateResult:
    """**G-19 fechado aqui.**

    A lacuna não era só a falta de um *gate*: no legado o harness **nunca roda
    em CI** — os 28 workflows não referenciam `evals/` de forma nenhuma. Medir
    sem nunca medir é a mesma coisa que não medir.

    Duas condições de reprovação, e a segunda é a que importa: **qualquer**
    identificador perdido reprova, independentemente do score. Um agregado
    alto com um SHA perdido esconde a falha mais cara da compactação.
    """
    perdidos = report.lost_identifiers
    if fail_on_lost_identifier and perdidos:
        return GateResult(
            False,
            report.score,
            threshold,
            f"{len(perdidos)} identificador(es) perdido(s) na compactação: "
            f"{[f.literal or f.text for f in perdidos]}. "
            f"Identificador perdido reprova independentemente do score — um "
            f"agregado alto esconde exatamente a falha mais cara.",
        )

    if report.score < threshold:
        return GateResult(
            False,
            report.score,
            threshold,
            f"recall de {report.score:.1%} abaixo do limiar de {threshold:.0%} "
            f"({report.recalled}/{report.total} fatos preservados)",
        )

    return GateResult(True, report.score, threshold, "recall dentro do limiar")
