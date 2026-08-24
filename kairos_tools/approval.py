"""A cadeia de aprovação de comando — 7 camadas, herdada como está.

T-27, decisão `questions.md#pergunta-12` (fecha **G-22**).

A ordem é a documentação. Cada camada existe porque alguma coisa passou por
onde não devia, e a **posição relativa** de duas delas é a propriedade mais
importante do módulo:

    1. gate de container       — backends isolados passam por cima de tudo
    2. blocklist hardline      — em código, nunca contornável
    3. guarda de sudo-stdin    — incondicional
    4. approvals.deny          ← ACIMA do bypass
    5. bypass de yolo / off    ← ABAIXO da negação do usuário
    6. command_allowlist       — permanente
    7. detecção de padrão      → perguntar

**`--yolo` amplia o que é permitido e nunca alcança o que foi proibido.**
Inverter 4 e 5 transformaria a regra do usuário em sugestão, e é o tipo de
troca que passa despercebida numa refatoração — por isso há teste que a fixa.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from enum import IntEnum, StrEnum

__all__ = [
    "HARDLINE_PATTERNS",
    "ApprovalContext",
    "Decision",
    "Layer",
    "Verdict",
    "detection_variants",
    "match_user_deny_rule",
    "resolve",
]


class Layer(IntEnum):
    """As 7 camadas, na ordem em que são consultadas.

    `IntEnum` para que a ordem seja comparável — e testável.
    """

    CONTAINER_SKIP = 1
    HARDLINE = 2
    SUDO_STDIN = 3
    USER_DENY = 4
    YOLO_BYPASS = 5
    ALLOWLIST = 6
    PATTERN_DETECTION = 7


class Verdict(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class Decision:
    verdict: Verdict
    layer: Layer
    reason: str
    #: O glob/padrão que decidiu, quando houve um.
    matched: str | None = None

    @property
    def bypassable(self) -> bool:
        """Esta negação pode ser contornada por `--yolo`?

        Só as camadas **abaixo** do bypass. É a propriedade central do
        módulo, exposta para que quem consulta não precise saber a ordem.
        """
        return self.layer > Layer.YOLO_BYPASS


@dataclass(frozen=True)
class ApprovalContext:
    """Tudo que a decisão precisa, explícito.

    Nenhum acesso a estado global aqui: a cadeia é uma função pura do
    comando mais este contexto, o que a torna testável sem montar um
    ambiente inteiro.
    """

    command: str
    #: Backend isolado (docker, ssh, modal…): o sandbox é a fronteira, e
    #: pedir aprovação para cada comando dentro dele seria teatro.
    isolated_backend: bool = False
    yolo: bool = False
    approvals_mode: str = "ask"
    #: Globs `fnmatch` de `approvals.deny` no config.yaml.
    user_deny: tuple[str, ...] = ()
    #: `command_allowlist` permanente.
    allowlist: tuple[str, ...] = ()
    #: O comando veio por stdin de um `sudo`?
    sudo_stdin: bool = False


# ---------------------------------------------------------------------------
# Camada 2 — blocklist hardline
# ---------------------------------------------------------------------------
#
# Em código, nunca editável pelo usuário, nunca contornável — nem por yolo,
# nem por allowlist. É o piso: o conjunto de coisas que o agente não faz
# porque nenhum caso de uso legítimo as justifica dentro de uma sessão de
# agente.

HARDLINE_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+(-[a-z]*[rf][a-z]*\s+)+/(\s|$)", "rm recursivo na raiz"),
    (r"\bmkfs(\.\w+)?\b", "formatação de sistema de arquivos"),
    (r"\bdd\s+.*\bof=/dev/(sd|nvme|hd)", "escrita direta em dispositivo de bloco"),
    (r":\(\)\s*\{.*\}\s*;?\s*:", "fork bomb"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "permissão total recursiva na raiz"),
    (r">\s*/dev/(sd|nvme|hd)\w*", "redirecionamento para dispositivo de bloco"),
    (r"\bshutdown\b|\breboot\b|\bhalt\b|\bpoweroff\b", "desligamento da máquina"),
)

_HARDLINE = tuple((re.compile(p, re.IGNORECASE), reason) for p, reason in HARDLINE_PATTERNS)


# ---------------------------------------------------------------------------
# Desofuscação
# ---------------------------------------------------------------------------

_DEOBFUSCATION = (
    (re.compile(r"\\(.)"), r"\1"),  # r\m -rf /  →  rm -rf /
    (re.compile(r"[\"']"), ""),  # git st""atus  →  git status
    (re.compile(r"\$\{IFS\}"), " "),  # cat${IFS}/etc/passwd
    (re.compile(r"\s+"), " "),  # espaço colapsado
)


def detection_variants(command: str) -> tuple[str, ...]:
    """As formas em que o comando é examinado.

    Truque de aspas e escape não pode escapar da regra do usuário **mais
    facilmente do que escapa da detecção de padrão perigoso**. As duas
    camadas veem exatamente as mesmas variantes — se divergissem, a
    ofuscação viraria caminho de contorno de uma e não da outra, o que é a
    pior combinação possível.
    """
    variants = [command.strip()]
    current = command.strip()
    for pattern, repl in _DEOBFUSCATION:
        current = pattern.sub(repl, current)
        if current not in variants:
            variants.append(current)
    return tuple(variants)


def match_user_deny_rule(command: str, patterns: tuple[str, ...]) -> str | None:
    """O glob de `approvals.deny` que casou, ou `None`.

    Casamento **case-insensitive** sobre todas as variantes desofuscadas.
    """
    globs = [p.strip().lower() for p in patterns if p and p.strip()]
    if not globs:
        return None
    for variant in detection_variants(command):
        candidate = variant.lower().strip()
        for glob in globs:
            if fnmatch.fnmatchcase(candidate, glob):
                return glob
    return None


def _match_hardline(command: str) -> tuple[str, str] | None:
    for variant in detection_variants(command):
        for pattern, reason in _HARDLINE:
            if pattern.search(variant):
                return pattern.pattern, reason
    return None


def _match_allowlist(command: str, patterns: tuple[str, ...]) -> str | None:
    globs = [p.strip().lower() for p in patterns if p and p.strip()]
    for variant in detection_variants(command):
        candidate = variant.lower().strip()
        for glob in globs:
            if fnmatch.fnmatchcase(candidate, glob):
                return glob
    return None


#: Classes que sempre pedem aprovação quando nada acima decidiu.
DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-[a-z]*[rf]", "remoção recursiva ou forçada"),
    (r"\bsudo\b", "elevação de privilégio"),
    (r"\bgit\s+push\s+.*(--force|-f)\b", "push forçado"),
    (r"\bcurl\b.*\|\s*(ba)?sh", "execução de conteúdo baixado"),
    (r"\bchmod\b|\bchown\b", "alteração de permissão"),
    (r"\b(docker|systemctl)\s+(stop|rm|kill|down)", "parada de serviço"),
    (r"\b(psql|mysql|sqlite3)\b.*\b(drop|truncate|delete)\b", "operação destrutiva em banco"),
)

_DANGEROUS = tuple((re.compile(p, re.IGNORECASE), reason) for p, reason in DANGEROUS_PATTERNS)


def _match_dangerous(command: str) -> tuple[str, str] | None:
    for variant in detection_variants(command):
        for pattern, reason in _DANGEROUS:
            if pattern.search(variant):
                return pattern.pattern, reason
    return None


# ---------------------------------------------------------------------------
# O ponto de entrada ÚNICO
# ---------------------------------------------------------------------------


def resolve(ctx: ApprovalContext) -> Decision:
    """Resolve a aprovação pela cadeia de 7 camadas, **na ordem**.

    É o único ponto de entrada de propósito: expor as camadas
    individualmente permitiria a um chamador consultá-las fora de ordem, e a
    ordem *é* a política. Um `if allowlist: allow` escrito antes do
    `user_deny` em qualquer lugar do código anularia a camada 4.
    """
    # 1 — Backend isolado passa por cima de tudo. A fronteira é o sandbox.
    if ctx.isolated_backend:
        return Decision(
            Verdict.ALLOW, Layer.CONTAINER_SKIP, "backend isolado: o sandbox é a fronteira"
        )

    # 2 — Hardline. Nunca contornável, nem por yolo, nem por allowlist.
    hard = _match_hardline(ctx.command)
    if hard is not None:
        return Decision(
            Verdict.DENY,
            Layer.HARDLINE,
            f"bloqueio permanente em código: {hard[1]}",
            matched=hard[0],
        )

    # 3 — sudo por stdin. Incondicional: é a forma de passar senha adiante
    #     sem que nada no comando visível denuncie a elevação.
    if ctx.sudo_stdin:
        return Decision(Verdict.DENY, Layer.SUDO_STDIN, "sudo alimentado por stdin não é permitido")

    # 4 — approvals.deny do usuário. ACIMA do bypass, e é o ponto do módulo.
    deny = match_user_deny_rule(ctx.command, ctx.user_deny)
    if deny is not None:
        return Decision(
            Verdict.DENY,
            Layer.USER_DENY,
            f"BLOQUEADO: este comando casa com a regra de negação definida pelo "
            f"usuário '{deny}' (approvals.deny em config.yaml). Ele não pode ser "
            f"executado pelo agente — nem com --yolo, /yolo ou approvals.mode=off. "
            f"NÃO tente de novo nem reformule o comando; o usuário o proibiu "
            f"explicitamente.",
            matched=deny,
        )

    # 5 — bypass. Amplia o permitido; nunca alcança o proibido acima.
    if ctx.yolo or ctx.approvals_mode == "off":
        return Decision(Verdict.ALLOW, Layer.YOLO_BYPASS, "yolo / approvals.mode=off")

    # 6 — allowlist permanente.
    allowed = _match_allowlist(ctx.command, ctx.allowlist)
    if allowed is not None:
        return Decision(
            Verdict.ALLOW,
            Layer.ALLOWLIST,
            f"casa com command_allowlist: {allowed}",
            matched=allowed,
        )

    # 7 — detecção de padrão perigoso → perguntar.
    dangerous = _match_dangerous(ctx.command)
    if dangerous is not None:
        return Decision(
            Verdict.ASK,
            Layer.PATTERN_DETECTION,
            f"padrão perigoso: {dangerous[1]}",
            matched=dangerous[0],
        )

    return Decision(Verdict.ALLOW, Layer.PATTERN_DETECTION, "nenhum padrão perigoso detectado")
