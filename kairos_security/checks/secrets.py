"""Verificação de segredos e tokens de API hardcoded no código ou logs."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from kairos_security.checks import OMITTED_LINE_EVIDENCE, regular_files
from kairos_security.models import Category, Finding, Severity

SECRET_PATTERNS: tuple[tuple[str, re.Pattern, Severity], ...] = (
    (
        "OpenAI API Key",
        re.compile(
            r"\b(?:sk-[a-zA-Z0-9]{20}T3BlbkFJ[a-zA-Z0-9]{20}"
            r"|sk-(?:proj|svcacct|admin)-"
            r"(?:[a-zA-Z0-9_-]{74}|[a-zA-Z0-9_-]{58})T3BlbkFJ"
            r"[a-zA-Z0-9_-]{58,})(?![a-zA-Z0-9_-])"
        ),
        Severity.CRITICAL,
    ),
    ("Anthropic API Key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{30,}"), Severity.CRITICAL),
    ("Google AI Studio Key", re.compile(r"AIzaSy[a-zA-Z0-9_-]{33}"), Severity.CRITICAL),
    ("Generic Bearer Token", re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{25,}"), Severity.HIGH),
)


def _eligible(path: Path) -> bool:
    return path.name != "auth.json" and (
        path.name == ".env"
        or path.name.startswith(".env.")
        or path.suffix in (".py", ".json", ".yaml", ".yml", ".ts", ".js", ".env")
    )


def check_hardcoded_secrets(
    code_root: Path, *, on_error: Callable[[], None] | None = None
) -> list[Finding]:
    findings: list[Finding] = []
    ignore_dirs = {".git", ".venv", "node_modules", "web_dist", "__pycache__", "dist"}

    for path, relative in regular_files(
        code_root, include=_eligible, ignored_dirs=ignore_dirs, on_error=on_error or (lambda: None)
    ):
        try:
            content = path.read_text(encoding="utf-8", errors="strict" if on_error else "ignore")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                if (
                    "test" in relative.parts
                    or "fake" in line
                    or "sk-test" in line
                    or "sk-ant-test" in line
                ):
                    continue

                for secret_name, pattern, sev in SECRET_PATTERNS:
                    if pattern.search(line):
                        findings.append(
                            Finding(
                                id="SEC-SECRET-001",
                                title=f"Possível {secret_name} exposta no código",
                                description=f"Detectado padrão correspondente a {secret_name}.",
                                severity=sev,
                                category=Category.SECRETS,
                                file_path=str(path),
                                line_number=line_idx,
                                remediation="Mova a chave para variáveis de ambiente ou utilize `kairos auth`.",
                                evidence=OMITTED_LINE_EVIDENCE,
                            )
                        )
        except (OSError, UnicodeError):
            if on_error:
                on_error()

    return findings
