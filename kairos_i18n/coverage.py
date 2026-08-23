"""Cobertura de idiomas **declarada por superfície**.

Decisão `questions.md#pergunta-10` (fecha **G-32**), Tarefa 06 / T-09.

**O que a decisão muda.** Nenhuma superfície ganha ou perde idioma. O que
muda é que a cobertura deixa de ser acidente herdado e vira decisão versionada
e testável: no legado, um usuário em português tinha CLI e dashboard
traduzidos e o aplicativo desktop em inglês, e **descobria isso sozinho**.

**Por que não convergir.** O catálogo do desktop é o maior dos três (3.350
linhas em `en.ts`, contra 878 na SPA e 456 no backend). Levá-lo a 17 idiomas
custaria ~40 mil linhas de tradução — o maior item de tradução do projeto — e
faria cada string nova do desktop custar 17 traduções para sempre. A cobertura
estreita é economicamente racional; o defeito nunca foi ela, foi ser invisível.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "Surface",
    "SurfaceCoverage",
    "COVERAGE",
    "coverage_for",
    "covers",
    "intends",
    "falls_back_to_english",
]


@dataclass(frozen=True)
class SurfaceCoverage:
    """O que uma superfície cobre — e o que ainda pretende cobrir.

    A distinção entre ``locales`` e ``target_locales`` é deliberada. O gate
    de paridade valida o que **existe**; o alvo registra o que o legado tinha
    e que ainda é trabalho de **tradução**, não de mecanismo. Sem os dois
    campos, ou o build fica vermelho por conteúdo que ninguém escreveu ainda,
    ou a dívida some de vista.
    """

    surface: str
    locales: frozenset[str]
    rationale: str
    target_locales: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.target_locales:
            object.__setattr__(self, "target_locales", self.locales)
        if not self.locales <= self.target_locales:
            raise ValueError(
                f"{self.surface}: idiomas fora do alvo declarado: "
                f"{sorted(self.locales - self.target_locales)}"
            )

    def covers(self, locale: str) -> bool:
        """Esta superfície mostra este idioma **hoje**?

        Baseado no que **entrega**, nunca no alvo. Dizer a um usuário japonês
        que o desktop cobre japonês, quando o catálogo ainda não existe,
        seria mentir com respaldo de um arquivo de declaração.
        """
        return locale in self.locales

    def intends(self, locale: str) -> bool:
        """Esta superfície **pretende** cobrir este idioma?

        É a pergunta de planejamento, separada da de runtime. Serve para o
        gate saber o que cobrar e para a dívida ficar visível — nunca para
        decidir o que dizer ao usuário.
        """
        return locale in self.target_locales

    @property
    def pending(self) -> frozenset[str]:
        """Idiomas do alvo ainda não escritos. Deve encolher."""
        return self.target_locales - self.locales


class Surface:
    BACKEND = "backend"
    SPA = "spa"
    DESKTOP = "desktop"


#: Os 17 do backend e da SPA no legado. O Kairos herda o conjunto como alvo.
_SEVENTEEN = frozenset({
    "af", "ar", "de", "en", "es", "fr", "ga", "hu", "it",
    "ja", "ko", "pt", "ru", "tr", "uk", "zh", "zh-hant",
})

COVERAGE: dict[str, SurfaceCoverage] = {
    Surface.BACKEND: SurfaceCoverage(
        surface=Surface.BACKEND,
        # O que o Kairos ENTREGA hoje. Os demais 14 do alvo são trabalho de
        # tradução — conteúdo humano, não reconstrução de mecanismo.
        locales=frozenset({"en", "pt", "es"}),
        target_locales=_SEVENTEEN,
        rationale="Catálogo pequeno (fatia fina, por desenho): o custo por idioma é baixo.",
    ),
    Surface.SPA: SurfaceCoverage(
        surface=Surface.SPA,
        locales=frozenset(),      # Tarefa 18
        target_locales=_SEVENTEEN,
        rationale="Rótulos de UI administrativa; paridade garantida pelo compilador.",
    ),
    Surface.DESKTOP: SurfaceCoverage(
        surface=Surface.DESKTOP,
        locales=frozenset(),      # Tarefa 19
        target_locales=frozenset({"ar", "en", "ja", "zh", "zh-hant"}),
        rationale=(
            "Catálogo ~4x maior que o da SPA (3.350 linhas). Expandir para 17 "
            "custaria ~40 mil linhas de tradução e tornaria cada string nova "
            "17x mais cara. Divergência INTENCIONAL, não omissão."
        ),
    ),
}


def coverage_for(surface: str) -> SurfaceCoverage:
    try:
        return COVERAGE[surface]
    except KeyError:
        raise KeyError(
            f"superfície desconhecida: {surface!r}. "
            f"Conhecidas: {sorted(COVERAGE)}"
        ) from None


def covers(surface: str, locale: str) -> bool:
    return coverage_for(surface).covers(locale)


def intends(surface: str, locale: str) -> bool:
    return coverage_for(surface).intends(locale)


def falls_back_to_english(surface: str, locale: str) -> bool:
    """O usuário verá inglês nesta superfície? (RF-16)

    É o que alimenta o aviso: em vez de a superfície simplesmente aparecer em
    inglês, ela informa que o idioma escolhido não é coberto **ali**.
    """
    return locale != "en" and not covers(surface, locale)
