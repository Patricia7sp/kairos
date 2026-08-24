"""As regras de negócio dos 7 grupos, classificadas.

``_reversa_sdd/domain.md`` §2.1–2.7.

**Por que existe uma classificação.** Os 7 grupos misturam duas naturezas
diferentes de regra, e tratá-las igual produziria código falso:

* ``RUNTIME`` — o sistema pode verificá-las em execução. Viram função com
  teste que as viola.
* ``PROCESS`` — regem como o projeto é **desenvolvido**, não como o software
  se comporta. *"Reject PRs that tell users to set X in .env"* e *"menus
  interativos de CLI devem usar curses"* são instruções para quem revisa
  código. Transformá-las em classe Python seria teatro: nenhuma execução as
  exercitaria, e o teste correspondente só provaria que a constante existe.

As ``PROCESS`` ficam registradas aqui como rubrica consultável, e são
reproduzidas em ``docs/rubrica-de-contribuicao.md`` para quem revisa.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = ["RULES", "Kind", "Rule", "by_group", "process_rules", "runtime_rules"]


class Kind(StrEnum):
    RUNTIME = "runtime"
    PROCESS = "process"


@dataclass(frozen=True)
class Rule:
    group: str
    statement: str
    kind: Kind
    #: Função do domínio que a impõe, ou a tarefa que vai impô-la.
    enforced_at: str


RULES: tuple[Rule, ...] = (
    # --- 2.1 Contrato de cache ---------------------------------------------
    Rule(
        "cache",
        "System prompt byte-estável pela vida da conversa",
        Kind.RUNTIME,
        "Tarefa 13 — agent",
    ),
    Rule("cache", "Alternância estrita de papéis", Kind.RUNTIME, "message.check_role_alternation"),
    Rule(
        "cache",
        "Nunca mensagem de usuário sintética mid-loop",
        Kind.RUNTIME,
        "message.check_no_synthetic_user_message",
    ),
    Rule(
        "cache",
        "Slash commands mutantes são cache-aware; invalidação diferida por default",
        Kind.RUNTIME,
        "Tarefa 15 — hermes-cli",
    ),
    Rule(
        "cache",
        "Modo de aprovação é config de perfil, não estado de conversa",
        Kind.RUNTIME,
        "Tarefa 15 — hermes-cli",
    ),
    Rule(
        "cache",
        "Compressão de contexto é a ÚNICA exceção autorizada",
        Kind.RUNTIME,
        "compaction (módulo inteiro)",
    ),
    # --- 2.2 Compactação (10) ----------------------------------------------
    Rule(
        "compaction",
        "1. Nunca cortar no meio de um par tool_call/tool_result",
        Kind.RUNTIME,
        "compaction.plan_boundary",
    ),
    Rule(
        "compaction",
        "2. As últimas mensagens reais sobrevivem",
        Kind.RUNTIME,
        "compaction.plan_boundary",
    ),
    Rule(
        "compaction",
        "3. A proteção da cabeça decai a cada ciclo",
        Kind.RUNTIME,
        "compaction.effective_protect_first_n",
    ),
    Rule(
        "compaction",
        "4. O sumarizador não pode fabricar turnos de usuário",
        Kind.RUNTIME,
        "compaction.validate_summary_provenance",
    ),
    Rule(
        "compaction",
        "5. Strikes duráveis, persistidos no banco",
        Kind.RUNTIME,
        "Session.compression_ineffective_count + schema",
    ),
    Rule(
        "compaction",
        "6. Falhas consecutivas escalam o cooldown",
        Kind.RUNTIME,
        "compaction.next_cooldown_seconds",
    ),
    Rule(
        "compaction",
        "7. UM session id para a vida; arquivar é active=0, não delete",
        Kind.RUNTIME,
        "Visibility + Lineage.is_listable",
    ),
    Rule(
        "compaction",
        "8. Mensagens chegadas durante a sumarização não são sumarizadas",
        Kind.RUNTIME,
        "Tarefa 05 — hermes-state (watermark)",
    ),
    Rule(
        "compaction",
        "9. A posse da lease é verificada DENTRO da transação de commit",
        Kind.RUNTIME,
        "Tarefa 05 — hermes-state",
    ),
    Rule(
        "compaction",
        "10. Identificadores são indexados mecanicamente, não confiados ao sumarizador",
        Kind.RUNTIME,
        "Tarefa 13 — agent",
    ),
    # --- 2.3 Propriedade e autonomia ---------------------------------------
    Rule(
        "ownership",
        "O Curador só poda o que ele mesmo criou",
        Kind.RUNTIME,
        "ownership.can_archive",
    ),
    Rule(
        "ownership",
        "Atores autônomos nunca fazem hard-delete; tudo é ledgerado",
        Kind.RUNTIME,
        "ownership.assert_can_hard_delete",
    ),
    Rule(
        "ownership",
        "Skills referenciadas por cron (inclusive pausado) são protegidas",
        Kind.RUNTIME,
        "ownership.can_archive",
    ),
    Rule(
        "ownership",
        "O sistema não confia na palavra do modelo; cruza com evidência de ferramenta",
        Kind.RUNTIME,
        "Tarefa 13 — agent",
    ),
    Rule(
        "ownership",
        "Job com subprocesso morto nunca reporta sucesso",
        Kind.RUNTIME,
        "scheduling.assert_not_reporting_success_when_dead",
    ),
    Rule(
        "ownership",
        "Capacidade agent-callable é propriedade da SESSÃO, não do processo",
        Kind.RUNTIME,
        "Tarefa 19 — apps-desktop",
    ),
    # --- 2.4 Credencial e segredo ------------------------------------------
    Rule(
        "credentials",
        ".env é só para segredos; comportamento vai em config.yaml",
        Kind.PROCESS,
        "credentials.assert_env_is_for_secrets (guarda) + rubrica",
    ),
    Rule(
        "credentials",
        "Rejeitar PRs que mandem 'set X in your .env' sem X ser credencial",
        Kind.PROCESS,
        "rubrica de contribuição",
    ),
    Rule(
        "credentials",
        "Intenção explícita do usuário vence OAuth logado obsoleto",
        Kind.RUNTIME,
        "Tarefa 15 — hermes-cli",
    ),
    Rule(
        "credentials",
        "Credencial one-time nunca é reusada",
        Kind.RUNTIME,
        "Tarefa 19 — apps-desktop",
    ),
    Rule(
        "credentials",
        "Só 401/403 confirmado significa reautenticar",
        Kind.RUNTIME,
        "credentials.should_reauthenticate",
    ),
    Rule(
        "credentials",
        "Um segredo tem UMA loja canônica; espelhos são cache derivado",
        Kind.RUNTIME,
        "Tarefa 15 — hermes-cli",
    ),
    Rule(
        "credentials",
        "Placeholders são rejeitados como segredo",
        Kind.RUNTIME,
        "credentials.has_usable_secret",
    ),
    # --- 2.5 Agendamento ----------------------------------------------------
    Rule(
        "scheduling",
        "One-shot reivindica o dispatch ANTES de executar",
        Kind.RUNTIME,
        "Tarefa 14 — cron (claim_dispatch)",
    ),
    Rule(
        "scheduling",
        "Backlog colapsa, mas o job dispara uma vez",
        Kind.RUNTIME,
        "scheduling.collapse_backlog",
    ),
    Rule(
        "scheduling",
        "Job enabled=true nunca aparece como pausado",
        Kind.RUNTIME,
        "scheduling.effective_job_state",
    ),
    Rule("scheduling", "Prompt de cron deve ser auto-contido", Kind.RUNTIME, "Tarefa 14 — cron"),
    Rule(
        "scheduling",
        "Falha da fonte de monitor é ERRO, nunca mudança",
        Kind.RUNTIME,
        "scheduling.monitor_hash_after_source_failure",
    ),
    Rule(
        "scheduling",
        "Jobs que reiniciam o gateway são rejeitados na criação",
        Kind.RUNTIME,
        "Tarefa 14 — cron (lifecycle_guard)",
    ),
    Rule("scheduling", "Sugestões nunca auto-criam jobs", Kind.RUNTIME, "Tarefa 14 — cron"),
    # --- 2.6 Superfície -----------------------------------------------------
    Rule(
        "surface",
        "Nada em background sequestra foco — 'Offer; don't hijack'",
        Kind.RUNTIME,
        "Tarefa 19 — apps-desktop",
    ),
    Rule("surface", "Visibilidade não é ciclo de vida", Kind.RUNTIME, "Tarefa 19 — apps-desktop"),
    Rule(
        "surface",
        "Só a superfície que o usuário olha publica na visão compartilhada",
        Kind.RUNTIME,
        "Tarefa 19 — apps-desktop",
    ),
    Rule(
        "surface",
        "Descrições de schema não mencionam ferramentas de outro toolset",
        Kind.PROCESS,
        "rubrica — o modelo alucinaria chamadas inexistentes",
    ),
    Rule(
        "surface",
        "Menus interativos de CLI devem usar curses",
        Kind.PROCESS,
        "rubrica de contribuição",
    ),
    Rule(
        "surface",
        "Caminhos de HOME nunca hardcoded — quebra perfis",
        Kind.RUNTIME,
        "connection.default_db_path (KAIROS_HOME)",
    ),
    # --- 2.7 O que o projeto rejeita ---------------------------------------
    Rule(
        "rejected",
        "Infraestrutura especulativa: hook sem consumidor concreto",
        Kind.PROCESS,
        "rubrica — exceto com caso de uso real declarado",
    ),
    Rule("rejected", "Novas env vars para config não-secreta", Kind.PROCESS, "rubrica"),
    Rule(
        "rejected",
        "Nova ferramenta core quando terminal+file ou uma skill resolvem",
        Kind.PROCESS,
        "rubrica — a Lei 2, cintura estreita",
    ),
    Rule(
        "rejected",
        "Testes detectores de mudança (congelar catálogos, contagens)",
        Kind.PROCESS,
        "rubrica",
    ),
)


def runtime_rules() -> tuple[Rule, ...]:
    return tuple(r for r in RULES if r.kind is Kind.RUNTIME)


def process_rules() -> tuple[Rule, ...]:
    return tuple(r for r in RULES if r.kind is Kind.PROCESS)


def by_group(group: str) -> tuple[Rule, ...]:
    return tuple(r for r in RULES if r.group == group)
