"""Bateria de testes defensivos contra Injeção de Prompt Indireta."""

from __future__ import annotations

from kairos_security.models import Category, Finding, Severity


def evaluate_prompt_hardening(system_prompt: str) -> list[Finding]:
    findings: list[Finding] = []

    if not system_prompt or len(system_prompt) < 20:
        findings.append(
            Finding(
                id="SEC-PROMPT-001",
                title="Prompt de sistema excessivamente simplificado ou ausente",
                description="Prompts de sistema sem delimitação de papel e restrições explícitas são mais vulneráveis a injeções.",
                severity=Severity.LOW,
                category=Category.PROMPT_INJECTION,
                remediation="Defina um system prompt claro instruindo o modelo a nunca desobedecer políticas de segurança.",
            )
        )

    return findings
