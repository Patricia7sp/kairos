"""Automation blueprints — catálogo único e tipado de automações agendadas.

Um *blueprint* é uma definição única de automação que cada superfície renderiza
nativamente:

  * Web / GUI        -> um formulário (um campo por slot)
  * CLI / messenger  -> um slash command pré-preenchido
  * Agente           -> um prompt-semente (é o que o ``fill`` usa)
  * Catálogo de docs -> um entry unificado com deep-link ``hermes://``

A fonte única é o schema de slots abaixo. ``blueprint_form_schema`` emite o que
um renderizador de formulário precisa; ``blueprint_slash_command`` emite o
comando de linha; ``blueprint_deeplink`` a URL; ``fill_blueprint`` valida os
valores e devolve os kwargs de ``JobStore.create`` — **sem segundo motor de
jobs**: o mesmo executor, guard de ciclo de vida e validação de delivery das
demais superfícies.

Critério do legado (T-11) preservado: usuários não digitam cron cru — o
blueprint carrega uma recorrência fixa em ``schedule_template`` e parametriza
só as partes amigáveis (hora, dia da semana).

Divergências kairos (documentadas):

- Sem lista de plataformas em lugar nenhum: o slot ``deliver`` não traz opções
  fechadas nem hardcoded (critério da T-10). ``local`` = só grava, sem entrega;
  qualquer outra coisa é um ``plataforma:destino`` validado a jusante pelo
  ``JobStore`` (e pela adequação dinâmica do adapter no gateway).
- ``origin`` do legado não existe em kairos (não há canal de origem); o valor
  é tratado como ``local``.
- Jobs kairos não carregam ``skills``: a lista fica como metadados e nunca
  entra nos kwargs de ``create``.
- Prompts do catálogo são autocontidos, sem referências a skills externas que
  não existem no kairos, e escritos em português (a superfície é do usuário).
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

__all__ = [
    "CATALOG",
    "WEEKDAY_PRESETS",
    "AutomationBlueprint",
    "BlueprintFillError",
    "BlueprintSlot",
    "blueprint_catalog_entry",
    "blueprint_deeplink",
    "blueprint_form_schema",
    "blueprint_seed_prompt",
    "blueprint_slash_command",
    "fill_blueprint",
    "get_blueprint",
    "parse_blueprint_slash",
]


class BlueprintFillError(ValueError):
    """Valores fornecidos para os slots não passaram na validação do catálogo."""


# Tipos de slot que os renderizadores entendem.
_SLOT_TYPES = frozenset({"time", "enum", "text", "weekdays"})

# Recorrências nomeadas -> campo dia-da-semana do cron.
WEEKDAY_PRESETS: dict[str, str] = {
    "everyday": "*",
    "weekdays": "1-5",
    "weekends": "0,6",
}

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
_DAY_TO_DOW = {
    "sunday": "0",
    "monday": "1",
    "tuesday": "2",
    "wednesday": "3",
    "thursday": "4",
    "friday": "5",
    "saturday": "6",
}


@dataclass(frozen=True)
class BlueprintSlot:
    """Um campo preenchível de um blueprint."""

    name: str
    type: str
    label: str
    default: Any = None
    options: tuple = ()
    optional: bool = False
    help: str = ""
    # False => ``options`` são sugestões, não um conjunto fechado (ex.: o slot
    # ``deliver``, cujo conjunto real de plataformas vem dos adapters).
    strict: bool = True

    def __post_init__(self) -> None:
        if self.type not in _SLOT_TYPES:
            raise ValueError(f"slot type desconhecido {self.type!r} (slot {self.name})")


@dataclass(frozen=True)
class AutomationBlueprint:
    """Uma automação agendada e parametrizada."""

    key: str
    title: str
    description: str
    category: str
    # Expressão cron com placeholders ``{slot}``, ex.
    # "{minute} {hour} * * {dow}". Cron literal sem placeholder = agenda fixa.
    schedule_template: str
    # Instrução-semente para o agente / prompt do job; pode conter {slot}s.
    prompt_template: str
    slots: list[BlueprintSlot] = field(default_factory=list)
    skills: tuple = ()
    tags: tuple = ()


def _time(default="08:00") -> BlueprintSlot:
    return BlueprintSlot(
        name="time",
        type="time",
        label="A que horas?",
        default=default,
        help="horário local 24h, ex.: 08:00",
    )


# Nenhuma lista de plataformas aqui: a forma ``plataforma:destino`` é validada
# no JobStore e a adequação dinâmica do adapter é do dispatcher.
_DELIVER = BlueprintSlot(
    name="deliver",
    type="text",
    label="Onde entregar?",
    default="local",
    strict=False,
    help="local = só grava, sem entrega; ou plataforma:destino de um adapter "
    "registrado no gateway (ex.: wpp:+5511999990000)",
)


CATALOG: list[AutomationBlueprint] = [
    AutomationBlueprint(
        key="morning-brief",
        title="Resumo da manhã",
        description="Um resumo curto diário: agenda de hoje, tempo e qualquer "
        "urgência aguardando você.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Produza um resumo matinal conciso para o usuário: eventos da "
            "agenda de hoje, o tempo local e itens urgentes. Mantenha curto e "
            "escaneável. Se nenhuma fonte de dados estiver conectada, dê um "
            "bom-dia breve com a data e ofereça conectar calendário e e-mail."
        ),
        slots=[_time("08:00"), _DELIVER],
        tags=("daily", "briefing"),
    ),
    AutomationBlueprint(
        key="daily-report",
        title="Relatório do dia",
        description="Um resumo diário: o que foi feito, o que ficou pendente e os próximos passos.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Escreva um relatório diário enxuto: o que já foi feito hoje, o "
            "que ficou em aberto e o que vem a seguir. Tom objetivo, formato "
            "escaneável."
        ),
        slots=[_time("19:00"), _DELIVER],
        tags=("daily", "report"),
    ),
    AutomationBlueprint(
        key="important-mail",
        title="Monitor de e-mail importante",
        description="Checa sua caixa periodicamente e avisa SÓ sobre e-mail "
        "que realmente precisa de atenção.",
        category="email",
        schedule_template="*/{interval_min} * * * *",
        prompt_template=(
            "Cheque a caixa de entrada do usuário por mensagens novas desde a "
            "última execução. Traga SOMENTE e-mails com: {criteria}. Se nada "
            "passar do filtro, responda [SILENT]. Se nenhuma fonte de e-mail "
            "estiver conectada, explique como conectar uma e pare."
        ),
        slots=[
            BlueprintSlot(
                name="interval_min",
                type="enum",
                label="De quanto em quanto?",
                default="30",
                options=("15", "30", "60"),
                help="minutos entre verificações",
            ),
            BlueprintSlot(
                name="criteria",
                type="text",
                label="Só me avise se o e-mail…",
                default="precisa de resposta hoje, é do meu gestor ou família, "
                "ou menciona um prazo",
            ),
            _DELIVER,
        ],
        tags=("email", "monitor"),
    ),
    AutomationBlueprint(
        key="weekly-review",
        title="Revisão semanal",
        description="Um resumo semanal: o que foi feito, o que segue aberto e o que vem por aí.",
        category="weekly",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Revise a semana completada do usuário e as próximas 1-2 semanas "
            "usando as fontes disponíveis (calendário, tarefas, notas, e-mail): "
            "aponte compromissos, projetos travados e itens aguardando. "
            "Recomendações e rascunhos apenas — sem mutações sem aprovação."
        ),
        slots=[
            _time("18:00"),
            BlueprintSlot(
                name="day",
                type="enum",
                label="Em qual dia?",
                default="sunday",
                options=("sunday", "monday", "friday", "saturday"),
            ),
            _DELIVER,
        ],
        tags=("weekly", "review"),
    ),
    AutomationBlueprint(
        key="weekly-report",
        title="Relatório semanal",
        description="Um relatório semanal consolidado das atividades e próximos passos.",
        category="weekly",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Escreva um relatório semanal consolidado: entregas da semana, "
            "impedimentos, métricas relevantes e plano para a próxima."
        ),
        slots=[
            _time("18:00"),
            BlueprintSlot(
                name="day",
                type="enum",
                label="Em qual dia?",
                default="friday",
                options=("sunday", "monday", "friday", "saturday"),
            ),
            _DELIVER,
        ],
        tags=("weekly", "report"),
    ),
    AutomationBlueprint(
        key="custom-reminder",
        title="Lembrete personalizado",
        description="Um lembrete recorrente nas suas palavras, na sua agenda.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template="Lembre o usuário: {what}",
        slots=[
            BlueprintSlot(
                name="what",
                type="text",
                label="Lembrar-me de…",
                default="fazer uma pausa e alongar",
            ),
            _time("14:00"),
            BlueprintSlot(
                name="recurrence",
                type="weekdays",
                label="Repetir em",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("reminder",),
    ),
    AutomationBlueprint(
        key="evening-winddown",
        title="Encerramento do dia",
        description="Um check-in ao fim do dia: a agenda de amanhã em um "
        "relance e o que preparar hoje à noite.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Dê ao usuário um encerramento curto do dia: a agenda de amanhã, "
            "compromissos cedo para se preparar e um empurrão gentil para "
            "fechar pendências de hoje. Calmo e breve — uma mensagem."
        ),
        slots=[_time("21:00"), _DELIVER],
        tags=("daily", "evening"),
    ),
    AutomationBlueprint(
        key="news-digest",
        title="Resumo de notícias por tópico",
        description="Um resumo recorrente sobre um tópico — deduplicado contra "
        "o que já foi enviado, então só item realmente novo chega.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Pesquise na web itens novos e relevantes sobre: {topic}. "
            "Deduplique contra o que você enviou nas execuções anteriores — "
            "só desenvolvimento genuinamente novo. Entregue um resumo compacto "
            "de no máximo {count} balas, cada uma em uma linha com link. Se "
            "nada for novo desde a última execução, responda [SILENT]."
        ),
        slots=[
            BlueprintSlot(
                name="topic",
                type="text",
                label="Qual tópico?",
                default="inteligência artificial e tecnologia",
                help="um assunto, produto, pessoa ou frase de busca",
            ),
            _time("18:00"),
            BlueprintSlot(
                name="recurrence",
                type="weekdays",
                label="Repetir em",
                default="weekdays",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            BlueprintSlot(
                name="count",
                type="enum",
                label="Quantas balas?",
                default="5",
                options=("3", "5", "8"),
            ),
            _DELIVER,
        ],
        tags=("digest", "research"),
    ),
    AutomationBlueprint(
        key="bill-renewal-watch",
        title="Aviso de contas e renovações",
        description="Um aviso antes de um pagamento recorrente, renovação de "
        "assinatura ou vencimento — para nada cobrar de surpresa.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Lembre o usuário de um pagamento ou renovação próxima: {what}. "
            "Formule como um aviso acionável (ex.: 'revise ou cancele antes "
            "de renovar'), não apenas uma notificação. Uma mensagem curta."
        ),
        slots=[
            BlueprintSlot(
                name="what",
                type="text",
                label="O que vence?",
                default="minha assinatura de streaming renova em breve",
            ),
            _time("10:00"),
            BlueprintSlot(
                name="recurrence",
                type="weekdays",
                label="Repetir em",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("reminder", "finance"),
    ),
    AutomationBlueprint(
        key="habit-checkin",
        title="Check-in de hábito",
        description="Um empurrão recorrente para manter um hábito no rumo e refletir sobre o dia.",
        category="general",
        schedule_template="{minute} {hour} * * {dow}",
        prompt_template=(
            "Empurre o usuário sobre o hábito: {habit}. Pergunte se ele "
            "conseguiu hoje, mantenha o tom caloroso e sem julgamento, e "
            "ofereça uma linha de incentivo. Uma mensagem curta."
        ),
        slots=[
            BlueprintSlot(
                name="habit",
                type="text",
                label="Qual hábito?",
                default="20 minutos de leitura",
            ),
            _time("20:00"),
            BlueprintSlot(
                name="recurrence",
                type="weekdays",
                label="Repetir em",
                default="everyday",
                options=tuple(WEEKDAY_PRESETS.keys()),
            ),
            _DELIVER,
        ],
        tags=("habit", "wellbeing"),
    ),
    AutomationBlueprint(
        key="hydration-move",
        title="Hidratação e movimento",
        description="Um empurrão periódico durante o dia para beber água, levantar e alongar.",
        category="general",
        # NOTE: passo no campo de minutos (*/90) dá volta por hora; use passo
        # no campo de horas para a cadência escolhida ser a que de fato dispara.
        schedule_template="0 {start_hour}-{end_hour}/{interval_hours} * * 1-5",
        prompt_template=(
            "Envie ao usuário um empurrão breve e amigável para beber água, "
            "levantar e alongar por um momento. Varie o texto a cada vez para "
            "não parecer robótico. Uma linha curta."
        ),
        slots=[
            BlueprintSlot(
                name="interval_hours",
                type="enum",
                label="De quanto em quanto?",
                default="1",
                options=("1", "2", "3"),
                help="horas entre empurrões",
            ),
            BlueprintSlot(
                name="start_hour",
                type="enum",
                label="Hora de início",
                default="9",
                options=("7", "8", "9", "10"),
                help="primeira hora da janela ativa (24h)",
            ),
            BlueprintSlot(
                name="end_hour",
                type="enum",
                label="Hora de fim",
                default="17",
                options=("16", "17", "18", "19"),
                help="última hora da janela ativa (24h)",
            ),
            _DELIVER,
        ],
        tags=("wellbeing", "focus"),
    ),
    AutomationBlueprint(
        key="on-this-day",
        title="Descobrimento do dia",
        description="Uma dose diária de curiosidade: um evento histórico "
        "notável, fato ou palavra do dia.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Dê ao usuário um item '{flavor}' para hoje — curto, surpreendente "
            "e genuinamente interessante. Uma ou duas frases, sem enrolação."
        ),
        slots=[
            BlueprintSlot(
                name="flavor",
                type="enum",
                label="Que tipo?",
                default="on this day in history",
                options=(
                    "on this day in history",
                    "word of the day",
                    "science fact",
                    "quote of the day",
                ),
            ),
            _time("07:30"),
            _DELIVER,
        ],
        tags=("daily", "curiosity"),
    ),
    AutomationBlueprint(
        key="agenda-lembrete",
        title="Lembrete da agenda",
        description="Avisa antes de um compromisso da agenda local (.ics): o "
        "agente só roda quando um evento entra na janela de lembretes e "
        "escreve o lembrete — sem custo nos ticks sem novidade.",
        category="general",
        # Cadência da checagem da agenda; o disparo real é a entrada de um
        # evento na janela (monitor calendar), não o cron em si. Cinco campos:
        # "*/N * * * *" = a cada N minutos no campo de minutos.
        schedule_template="*/{interval_min} * * * *",
        prompt_template=(
            "Escreva um lembrete breve e acionável sobre o(s) evento(s) da "
            "agenda abaixo, no tom {tom}. Uma mensagem curta."
        ),
        slots=[
            BlueprintSlot(
                name="interval_min",
                type="enum",
                label="Checar a agenda a cada",
                default="5",
                options=("5", "10", "15"),
                help="minutos entre checagens da agenda",
            ),
            BlueprintSlot(
                name="janela_min",
                type="text",
                label="Antecedência do lembrete (min)",
                default="120",
                help="minutos antes do início; 1–1440",
            ),
            BlueprintSlot(
                name="tom",
                type="enum",
                label="Tom",
                default="natural",
                options=("natural", "formal", "brincalhão"),
            ),
            _DELIVER,
        ],
        tags=("reminder", "calendar"),
    ),
]

_CATALOG_BY_KEY = {r.key: r for r in CATALOG}


def get_blueprint(key: str) -> AutomationBlueprint | None:
    return _CATALOG_BY_KEY.get(key)


# ---------------------------------------------------------------------------
# Renderizadores — as quatro superfícies a partir do mesmo schema
# ---------------------------------------------------------------------------


def blueprint_form_schema(blueprint: AutomationBlueprint) -> dict[str, Any]:
    """JSON que um renderizador de formulário precisa para este blueprint."""
    return {
        "key": blueprint.key,
        "title": blueprint.title,
        "description": blueprint.description,
        "category": blueprint.category,
        "tags": list(blueprint.tags),
        "fields": [
            {
                "name": s.name,
                "type": s.type,
                "label": s.label,
                "default": s.default,
                "options": list(s.options),
                "optional": s.optional,
                "strict": s.strict,
                "help": s.help,
            }
            for s in blueprint.slots
        ],
    }


def blueprint_slash_command(
    blueprint: AutomationBlueprint, values: dict[str, Any] | None = None
) -> str:
    """Comando ``/blueprint <key> slot=val ...``, pronto para colar.

    Usa o default de cada slot quando ``values`` é omitido. Slots de texto
    livre são citados."""
    values = values or {}
    parts = [f"/blueprint {blueprint.key}"]
    for s in blueprint.slots:
        val = values.get(s.name, s.default)
        if val is None or val == "":
            if s.optional:
                continue
            val = ""
        sval = str(val)
        if s.type == "text" or " " in sval:
            sval = '"' + sval.replace('"', '\\"') + '"'
        parts.append(f"{s.name}={sval}")
    return " ".join(parts)


def parse_blueprint_slash(command: str) -> tuple[str, dict[str, str]]:
    """Decodifica o slash command de volta em ``(key, values)``.

    Permite que qualquer superfície (CLI, chat, deep-link) consuma a mesma
    serialização gerada por ``blueprint_slash_command``."""
    tokens = shlex.split(command)
    if not tokens or tokens[0] != "/blueprint":
        raise ValueError("slash command deve começar com /blueprint")
    if len(tokens) < 2:
        raise ValueError("slash command exige a chave do blueprint")
    key = tokens[1]
    values: dict[str, str] = {}
    for token in tokens[2:]:
        if "=" not in token:
            raise ValueError(f"par de slot inválido: {token!r} (esperado slot=valor)")
        name, _, value = token.partition("=")
        values[name] = value
    return key, values


def blueprint_deeplink(blueprint: AutomationBlueprint, values: dict[str, Any] | None = None) -> str:
    """Deep-link ``hermes://blueprint/<key>?slot=val`` (abre o formulário)."""
    values = values or {}
    query = {}
    for s in blueprint.slots:
        val = values.get(s.name, s.default)
        if val not in (None, ""):
            query[s.name] = str(val)
    qs = ("?" + urlencode(query)) if query else ""
    return f"hermes://blueprint/{quote(blueprint.key)}{qs}"


def blueprint_seed_prompt(
    blueprint: AutomationBlueprint, values: dict[str, Any] | None = None
) -> str:
    """Prompt-semente com os slots preenchidos pelos valores/defaults."""
    resolved = {}
    for s in blueprint.slots:
        val = values.get(s.name, s.default) if values else s.default
        if val not in (None, ""):
            resolved[s.name] = val
    try:
        return blueprint.prompt_template.format(**resolved)
    except KeyError as exc:
        raise BlueprintFillError(f"prompt do blueprint sem valor para {exc}") from exc


def _humanize_schedule(blueprint: AutomationBlueprint) -> str:
    """Descrição curta em português de quando o blueprint roda (defaults)."""
    sched = blueprint.schedule_template
    if sched.startswith("*/"):
        iv = next((s for s in blueprint.slots if s.name == "interval_min"), None)
        every = (iv.default if iv else None) or sched.split("/")[1].split()[0]
        return f"a cada {every} minutos"
    if "{interval_hours}" in sched:
        iv = next((s for s in blueprint.slots if s.name == "interval_hours"), None)
        every = str((iv.default if iv else None) or "1")
        scope = "dias úteis, " if "* * 1-5" in sched else ""
        return f"{scope}a cada hora" if every == "1" else f"{scope}a cada {every} horas"
    time_slot = next((s for s in blueprint.slots if s.type == "time"), None)
    when = time_slot.default if time_slot else None
    if "* * 1-5" in sched:
        return f"dias úteis às {when}" if when else "todo dia útil"
    if "{dow}" in sched:
        day_slot = next((s for s in blueprint.slots if s.name in ("day", "recurrence")), None)
        scope = (day_slot.default if day_slot else "") or ""
        if scope and when:
            return f"{scope} às {when}"
        return f"às {when}" if when else "em uma agenda"
    if when:
        return f"diário às {when}"
    return "em uma agenda"


def blueprint_catalog_entry(blueprint: AutomationBlueprint) -> dict[str, Any]:
    """Shape unificado para o catálogo (docs / API / painel).

    Combina as quatro superfícies — formulário, slash command, prompt-semente e
    deep-link — além da descrição amigável da agenda."""
    return {
        **blueprint_form_schema(blueprint),
        "schedule": blueprint.schedule_template,
        "scheduleHuman": _humanize_schedule(blueprint),
        "command": blueprint_slash_command(blueprint),
        "seedPrompt": blueprint_seed_prompt(blueprint),
        "appUrl": blueprint_deeplink(blueprint),
    }


# ---------------------------------------------------------------------------
# Fill + validação -> kwargs de JobStore.create (sem segundo motor de jobs)
# ---------------------------------------------------------------------------


def _resolve_schedule(  # noqa: PLR0912 - templates cobrem os placeholders do legado
    blueprint: AutomationBlueprint, values: dict[str, Any]
) -> str:
    """Preenche os placeholders do schedule_template com os valores dos slots."""
    sched = blueprint.schedule_template

    repl: dict[str, str] = {}

    time_val = values.get("time")
    if "{minute}" in sched or "{hour}" in sched:
        if not time_val:
            raise BlueprintFillError("a hora é obrigatória")
        m = _TIME_RE.match(str(time_val).strip())
        if not m:
            raise BlueprintFillError(f"hora inválida {time_val!r} — use HH:MM (24h)")
        repl["hour"] = str(int(m.group(1)))
        repl["minute"] = str(int(m.group(2)))

    if "{dow}" in sched:
        if "recurrence" in values:
            preset = str(values.get("recurrence", "everyday")).lower()
            if preset not in WEEKDAY_PRESETS:
                raise BlueprintFillError(
                    f"recorrência desconhecida {preset!r} — uma de {', '.join(WEEKDAY_PRESETS)}"
                )
            repl["dow"] = WEEKDAY_PRESETS[preset]
        elif "day" in values:
            day = str(values.get("day", "")).lower()
            if day not in _DAY_TO_DOW:
                raise BlueprintFillError(f"dia desconhecido {day!r}")
            repl["dow"] = _DAY_TO_DOW[day]
        else:
            repl["dow"] = "*"

    if "{interval_min}" in sched:
        iv = str(values.get("interval_min", "")).strip()
        if not iv.isdigit() or int(iv) <= 0:
            raise BlueprintFillError(f"intervalo inválido {iv!r} — minutos inteiro positivo")
        repl["interval_min"] = iv

    for name in re.findall(r"\{(\w+)\}", sched):
        if name not in repl and name in values:
            repl[name] = str(values[name])

    try:
        return sched.format(**repl)
    except KeyError as exc:  # pragma: no cover - template/slot mismatch é erro dev
        raise BlueprintFillError(f"schedule sem valor para {exc}") from exc


def fill_blueprint(blueprint: AutomationBlueprint, values: dict[str, Any]) -> dict[str, Any]:
    """Valida ``values`` e devolve kwargs de ``JobStore.create``.

    Slots obrigatórios ausentes levantam ``BlueprintFillError`` nomeando o slot
    (para o formulário marcar o campo e o agente saber o que pedir). Slots
    desconhecidos são rejeitados. Valores enum são conferidos contra as opções
    quando ``strict``. O resultado aponta direto para ``create`` — o guard de
    ciclo de vida, a validação de delivery e o executor são os mesmos das
    demais superfícies."""
    known = {s.name for s in blueprint.slots}
    unknown = sorted(set(values) - known)
    if unknown:
        raise BlueprintFillError(
            f"slot{'s' if len(unknown) > 1 else ''} desconhecido{'s' if len(unknown) > 1 else ''}: "
            f"{', '.join(unknown)} — válidos: {', '.join(s.name for s in blueprint.slots)}"
        )
    resolved: dict[str, Any] = {}
    for s in blueprint.slots:
        raw = values.get(s.name, s.default)
        if raw in (None, ""):
            if s.optional:
                continue
            raise BlueprintFillError(f"valor obrigatório ausente: {s.name} ({s.label})")
        if (
            s.type == "enum"
            and s.strict
            and s.options
            and str(raw) not in {str(o) for o in s.options}
        ):
            raise BlueprintFillError(
                f"{s.name}={raw!r} não permitido — uma de {', '.join(map(str, s.options))}"
            )
        resolved[s.name] = raw

    schedule = _resolve_schedule(blueprint, resolved)
    prompt = blueprint_seed_prompt(blueprint, resolved)

    monitor = None
    raw_jawela = resolved.get("janela_min")
    if raw_jawela is not None:
        from kairos_cron.calendar_monitor import MAX_WINDOW_MINUTES

        window_raw = str(raw_jawela).strip()
        if not window_raw.isdigit() or not 1 <= int(window_raw) <= MAX_WINDOW_MINUTES:
            raise BlueprintFillError(
                f"janela_min={raw_jawela!r} inválido — inteiro entre 1 e {MAX_WINDOW_MINUTES}"
            )
        monitor = {"type": "calendar", "janela_min": int(window_raw)}

    deliver = str(resolved.get("deliver", "")).strip() or "local"
    delivery = None
    if deliver not in ("local", "origin"):
        delivery = {"target": deliver}

    return {
        "name": blueprint.title,
        "prompt": prompt,
        "schedule": {"kind": "cron", "expr": schedule},
        "monitor": monitor,
        "delivery": delivery,
    }
