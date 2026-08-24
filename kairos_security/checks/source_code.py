"""Análise estática do código-fonte do próprio Kairos.

Os demais checks auditam o *runtime* (`~/.kairos`): permissões de arquivo,
credenciais gravadas, guardrails carregados em memória. Este aqui audita o
que está escrito no repositório — a classe de problema que só aparece
lendo o código, nunca inspecionando a instalação.

As regras não são genéricas: cada uma corresponde a um defeito que já foi
encontrado neste projeto ou que custa caro o bastante para valer o
falso-positivo ocasional. A justificativa está junto de cada padrão.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kairos_security.models import Category, Finding, Severity

# Diretórios que nunca são código-fonte auditável deste projeto.
IGNORE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "web_dist",  # bundle compilado: minificado, não é fonte
        "__pycache__",
        "dist",
        "build",
        ".ruff_cache",
        ".pytest_cache",
        "locales",
    }
)

SOURCE_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})


# Valores que anunciam não ser segredo de verdade.
PLACEHOLDERS = (
    "test", "fake", "dummy", "example", "sample", "placeholder", "changeme",
    "your-", "your_", "xxx", "redacted", "<", "{", "$", "...",
)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _is_real_credential(line: str, match: re.Match[str]) -> bool:
    """Separa segredo de verdade de constante que só *parece* um.

    Dois casos dominaram os falsos-positivos neste repositório: a constante
    de enum cujo valor repete o próprio nome (`DIRECT_API_KEY =
    "direct_api_key"`) e o placeholder óbvio de teste. Nenhum dos dois é
    credencial — mas `kairos-session-token`, que era o bug real, também não
    tem dígito nem maiúscula, então filtrar por entropia descartaria
    justamente o achado que motivou a regra. O discriminador que funciona é
    o eco do nome.
    """
    key, value = match.group(1), match.group(2)
    if _normalize(value) == _normalize(key):
        return False
    # `TOKEN_HEADER = "X-Hermes-Session-Token"` guarda o *nome* do cabeçalho,
    # não o segredo que trafega nele.
    if key.upper().endswith(("_HEADER", "_NAME", "_FIELD", "_PARAM", "_PREFIX")):
        return False
    if value.upper().startswith(("X-", "HTTP_", "BEARER ")):
        return False
    return all(bad not in value.lower() for bad in PLACEHOLDERS)


def _md5_is_for_security(line: str, match: re.Match[str]) -> bool:
    """`usedforsecurity=False` já declara que o hash é só chave de cache."""
    return "usedforsecurity=False" not in line.replace(" ", "")


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern[str]
    title: str
    description: str
    severity: Severity
    remediation: str
    # Filtro extra: recebe a linha e o match, devolve False para descartar.
    validate: Callable[[str, re.Match[str]], bool] | None = None


RULES: tuple[Rule, ...] = (
    Rule(
        id="SEC-SRC-001",
        # Só interessa quando é *valor atribuído* — em texto de --help o
        # literal é documentação, não configuração.
        pattern=re.compile(r"=\s*[\"']0\.0\.0\.0[\"']"),
        title="Serviço com escuta em todas as interfaces (0.0.0.0) por padrão",
        description=(
            "Vincular a 0.0.0.0 por padrão expõe o serviço a toda a rede local. "
            "Para uma UI local o padrão seguro é o loopback, com a exposição "
            "sendo uma escolha explícita de quem executa."
        ),
        severity=Severity.HIGH,
        remediation='Use "127.0.0.1" como padrão e permita 0.0.0.0 via flag explícita.',
    ),
    Rule(
        id="SEC-SRC-002",
        pattern=re.compile(
            r"(?i)(\w*(?:session[_-]?token|auth[_-]?token|access[_-]?token|api[_-]?key"
            # `_*` final aceita `__SESSION_TOKEN__`; o `\b` impede que
            # `token` case dentro de `tokenize`.
            r"|secret|password|passwd|token|ticket)_*)\b"
            # A aspa opcional cobre a chave de dicionário/JSON: {"token": "..."}.
            r"[\"']?\s*[:=]\s*[\"']([^\"']{8,})[\"']"
        ),
        title="Credencial constante embutida no código",
        description=(
            "Um segredo fixo no código é público: está no repositório, no bundle "
            "entregue ao navegador e igual em toda instalação."
        ),
        severity=Severity.HIGH,
        remediation="Gere por processo com `secrets.token_urlsafe`, ou leia de variável de ambiente.",
        validate=_is_real_credential,
    ),
    Rule(
        id="SEC-SRC-003",
        pattern=re.compile(r"(?i)(auth[_-]?required|require[_-]?auth)\s*[:=]\s*(false|False|0)\b"),
        title="Autenticação desativada no código",
        description="Um serviço que aceita qualquer requisição depende só do bind para se proteger.",
        severity=Severity.MEDIUM,
        remediation="Valide um token por sessão em todas as rotas que não sejam healthcheck.",
    ),
    Rule(
        id="SEC-SRC-004",
        pattern=re.compile(r"shell\s*=\s*True"),
        title="Subprocesso executado através do shell",
        description=(
            "Com shell=True qualquer conteúdo interpolado no comando vira injeção. "
            "O agente monta comandos a partir de saída de modelo — o risco é concreto."
        ),
        severity=Severity.HIGH,
        remediation="Passe a lista de argumentos sem shell=True.",
    ),
    Rule(
        id="SEC-SRC-005",
        pattern=re.compile(r"\b(eval|exec)\s*\(", re.NOFLAG),
        title="Uso de eval/exec",
        description="Avaliar código em tempo de execução transforma qualquer dado em código.",
        severity=Severity.HIGH,
        remediation="Substitua por `json.loads`, `ast.literal_eval` ou um despacho explícito.",
    ),
    Rule(
        id="SEC-SRC-006",
        pattern=re.compile(r"yaml\.load\s*\((?!.*SafeLoader)"),
        title="yaml.load sem SafeLoader",
        description="O loader padrão do PyYAML instancia objetos arbitrários do documento.",
        severity=Severity.HIGH,
        remediation="Use `yaml.safe_load` ou passe `Loader=yaml.SafeLoader`.",
    ),
    Rule(
        id="SEC-SRC-007",
        pattern=re.compile(r"verify\s*=\s*False"),
        title="Verificação de certificado TLS desabilitada",
        description="Sem validar o certificado, a conexão aceita qualquer interceptador.",
        severity=Severity.HIGH,
        remediation="Remova `verify=False`; para CA interna aponte `verify` ao bundle correto.",
    ),
    Rule(
        id="SEC-SRC-008",
        # `==` em segredo vaza o prefixo correto por tempo de resposta.
        pattern=re.compile(
            r"(?i)if\s+.*\b(token|secret|password|signature|hmac|api[_-]?key)\b[^=!<>]*==(?!=)"
        ),
        title="Comparação de segredo sensível a timing",
        description=(
            "`==` sai no primeiro byte diferente; o tempo de resposta revela quantos "
            "caracteres do segredo já estão certos."
        ),
        severity=Severity.MEDIUM,
        remediation="Compare com `hmac.compare_digest`, que roda em tempo constante.",
    ),
    Rule(
        id="SEC-SRC-009",
        pattern=re.compile(r"(?i)\b(md5|sha1)\s*\("),
        title="Hash criptograficamente quebrado (MD5/SHA-1)",
        description="MD5 e SHA-1 têm colisões práticas e não servem para uso de segurança.",
        severity=Severity.MEDIUM,
        remediation="Use SHA-256; se for só chave de cache, marque `usedforsecurity=False`.",
        validate=_md5_is_for_security,
    ),
    Rule(
        id="SEC-SRC-010",
        pattern=re.compile(r"[\"']/tmp/"),
        title="Caminho fixo em /tmp",
        description=(
            "Um nome previsível em diretório compartilhado permite que outro usuário "
            "da máquina crie o arquivo antes (symlink attack)."
        ),
        severity=Severity.LOW,
        remediation="Use `tempfile.mkstemp` ou `TemporaryDirectory`.",
    ),
)

# `except Exception: pass` engole *qualquer* falha sem deixar rastro. Só o
# except amplo entra: `except UnicodeDecodeError: continue` num laço que
# tenta encodings alternativos é fluxo de controle legítimo, não erro
# escondido — acusá-lo treinaria o leitor a ignorar a categoria inteira.
_EXCEPT_RE = re.compile(r"^\s*except\s*(\(?\s*(Exception|BaseException)\b[^:]*)?:\s*(#.*)?$")
_PASS_RE = re.compile(r"^\s*(pass|continue)\s*(#.*)?$")


def _is_test_file(path: Path) -> bool:
    if "tests" in path.parts or "__tests__" in path.parts:
        return True
    name = path.name
    return (
        name.startswith("test_")
        or name.endswith(("_test.py", ".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )


def _should_skip(path: Path) -> bool:
    if any(part in IGNORE_DIRS for part in path.parts):
        return True
    if path.suffix not in SOURCE_SUFFIXES:
        return True
    # Código de teste não é auditado: a fixture é insegura de propósito
    # (`api_key="sk-ant-test"`, `path="/tmp/a.txt"`) e a suíte deste próprio
    # scanner precisa conter todos os payloads que ele procura. Acusá-los
    # encheria o relatório de ruído e treinaria o leitor a ignorá-lo.
    if _is_test_file(path):
        return True
    # Mesma razão para o arquivo de regras: cada padrão encontraria a si mesmo.
    return path.name == "source_code.py"


def _is_noise(line: str) -> bool:
    """Comentário puro e diretiva de lint não são código executado."""
    stripped = line.strip()
    return stripped.startswith(("#", "//", "*")) or "noqa" in stripped


def scan_source_tree(source_root: Path) -> list[Finding]:
    """Percorre o código-fonte aplicando as regras estáticas."""
    findings: list[Finding] = []
    if not source_root.is_dir():
        return findings

    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or _should_skip(path):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        try:
            shown = str(path.relative_to(source_root))
        except ValueError:
            shown = str(path)

        lines = content.splitlines()
        for idx, line in enumerate(lines, start=1):
            # Este check roda antes do filtro de ruído de propósito: um
            # supressor de linter silencia o ruff, não a revisão de segurança.
            if _EXCEPT_RE.match(line) and idx < len(lines) and _PASS_RE.match(lines[idx]):
                findings.append(
                    Finding(
                        id="SEC-SRC-011",
                        title="Exceção ampla silenciada sem registro",
                        description=(
                            "`except Exception: pass` descarta qualquer falha sem deixar "
                            "rastro — o erro some e o código segue com estado inesperado."
                        ),
                        severity=Severity.LOW,
                        category=Category.SOURCE_CODE,
                        file_path=shown,
                        line_number=idx,
                        remediation="Registre com `logger.warning` antes de degradar.",
                        evidence=line.strip()[:120],
                    )
                )

            if _is_noise(line):
                continue

            for rule in RULES:
                match = rule.pattern.search(line)
                if match and (rule.validate is None or rule.validate(line, match)):
                    findings.append(
                        Finding(
                            id=rule.id,
                            title=rule.title,
                            description=rule.description,
                            severity=rule.severity,
                            category=Category.SOURCE_CODE,
                            file_path=shown,
                            line_number=idx,
                            remediation=rule.remediation,
                            evidence=line.strip()[:120],
                        )
                    )

    return findings
