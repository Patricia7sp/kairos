"""Orquestrador do motor de auditoria de segurança (SecurityAuditor)."""

from __future__ import annotations

import time
from pathlib import Path

from kairos_security.checks.agent_guardrails import check_agent_policy_invariants
from kairos_security.checks.api_surface import check_api_security_configuration
from kairos_security.checks.path_traversal import run_path_traversal_tests
from kairos_security.checks.permissions import check_file_permissions
from kairos_security.checks.prompt_injection import evaluate_prompt_hardening
from kairos_security.checks.secrets import check_hardcoded_secrets
from kairos_security.models import Finding, SecurityReport


class SecurityAuditor:
    """Executa varreduras de segurança estáticas e dinâmicas no Kairos."""

    def __init__(self, target_dir: Path | None = None) -> None:
        self.target_dir = target_dir or Path.cwd()

    def run_audit(self, system_prompt: str = "") -> SecurityReport:
        t0 = time.time()
        findings: list[Finding] = []

        # 1. Permissões de arquivos
        findings.extend(check_file_permissions(self.target_dir))

        # 2. Segredos expostos
        findings.extend(check_hardcoded_secrets(self.target_dir))

        # 3. Path traversal guards
        findings.extend(run_path_traversal_tests(self.target_dir))

        # 4. Guardrails e políticas de agente
        findings.extend(check_agent_policy_invariants())

        # 5. API Surface
        try:
            from kairos_web.server import app

            cors_middleware = next(
                (m for m in getattr(app, "user_middleware", []) if "CORS" in str(m.cls)), None
            )
            origins = (
                cors_middleware.kwargs.get("allow_origins", [])
                if cors_middleware and hasattr(cors_middleware, "kwargs")
                else []
            )
        except Exception:  # noqa: BLE001
            origins = []
        findings.extend(check_api_security_configuration(origins))

        # 6. Prompt Injection Hardening
        default_prompt = system_prompt or "Você é o Kairos, um assistente agentic de programação."
        findings.extend(evaluate_prompt_hardening(default_prompt))

        duration = (time.time() - t0) * 1000
        return SecurityReport(
            target=str(self.target_dir),
            findings=findings,
            duration_ms=round(duration, 2),
        )


def run_full_audit(target_dir: Path | None = None) -> SecurityReport:
    auditor = SecurityAuditor(target_dir)
    return auditor.run_audit()
