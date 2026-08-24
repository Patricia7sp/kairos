"""Verificação de segredos e tokens de API hardcoded no código ou logs."""

from __future__ import annotations

import re
from pathlib import Path

from kairos_security.models import Category, Finding, Severity

SECRET_PATTERNS: tuple[tuple[str, re.Pattern, Severity], ...] = (
    (
        "OpenAI API Key",
        re.compile(r"sk-[a-zA-Z0-9]{20,T3BlbkFJ[a-zA-Z0-9]{20,}"),
        Severity.CRITICAL,
    ),
    ("Anthropic API Key", re.compile(r"sk-ant-[a-zA-Z0-9_-]{30,}"), Severity.CRITICAL),
    ("Google AI Studio Key", re.compile(r"AIzaSy[a-zA-Z0-9_-]{33}"), Severity.CRITICAL),
    ("Generic Bearer Token", re.compile(r"(?i)bearer\s+[a-zA-Z0-9_\-\.]{25,}"), Severity.HIGH),
)


def check_hardcoded_secrets(code_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    ignore_dirs = {".git", ".venv", "node_modules", "web_dist", "__pycache__", "dist"}

    for path in code_root.rglob("*"):
        if any(ignored in path.parts for ignored in ignore_dirs):
            continue
        if not path.is_file() or path.suffix not in (
            ".py",
            ".json",
            ".yaml",
            ".yml",
            ".ts",
            ".js",
            ".env",
        ):
            continue
        if path.name == "auth.json":
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
            for line_idx, line in enumerate(content.splitlines(), start=1):
                if (
                    "test" in path.parts
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
                                evidence=line[:50] + "...",
                            )
                        )
        except Exception:  # noqa: BLE001, S112
            continue

    return findings
