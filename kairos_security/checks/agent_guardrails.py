"""Auditoria das políticas de aprovação e guardrails do agente."""

from __future__ import annotations

from kairos_security.models import Category, Finding, Severity
from kairos_tools.policy import DELEGATE_BLOCKED_TOOLS, delegate_block_reason


def check_agent_policy_invariants() -> list[Finding]:
    findings: list[Finding] = []
    must_block = ["delegate_task", "clarify", "memory", "send_message", "cronjob"]

    for tool_name in must_block:
        if tool_name not in DELEGATE_BLOCKED_TOOLS:
            findings.append(
                Finding(
                    id="SEC-GUARD-001",
                    title=f"Ferramenta crítica {tool_name} não está bloqueada para sub-agentes delegados",
                    description=f"Sub-agentes não devem executar {tool_name} sem supervisão direta.",
                    severity=Severity.CRITICAL,
                    category=Category.AGENT_GUARDRAILS,
                    remediation=f"Adicione {tool_name!r} em DELEGATE_BLOCKED_TOOLS em kairos_tools/policy.py",
                )
            )

    reason = delegate_block_reason("delegate_task")
    if not reason:
        findings.append(
            Finding(
                id="SEC-GUARD-002",
                title="Motivo de bloqueio de ferramenta ausente",
                description="delegate_block_reason não retornou justificativa para ferramenta bloqueada.",
                severity=Severity.LOW,
                category=Category.AGENT_GUARDRAILS,
                remediation="Implemente delegate_block_reason para todas as ferramentas restritas.",
            )
        )

    return findings
