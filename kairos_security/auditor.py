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
from kairos_security.checks.source_code import scan_source_tree
from kairos_security.models import Finding, SecurityReport


def find_source_root(start: Path | None = None) -> Path | None:
    """Sobe a árvore até achar a raiz do repositório do Kairos.

    O alvo de runtime (`~/.kairos`) e o código-fonte são lugares diferentes:
    auditar um não diz nada sobre o outro. Como o `pyproject.toml` marca a
    raiz, dá para encontrá-la mesmo quando o comando roda de outro diretório
    — inclusive a partir do próprio pacote instalado em modo editável.
    """
    for base in (start, Path.cwd(), Path(__file__).resolve().parent):
        if base is None:
            continue
        for candidate in (base.resolve(), *base.resolve().parents):
            if (candidate / "pyproject.toml").is_file():
                return candidate
    return None


class SecurityAuditor:
    """Executa varreduras de segurança estáticas e dinâmicas no Kairos.

    `target_dir` é a instalação em runtime; `source_root` é o repositório.
    Quando o segundo não é informado, é procurado — e se não existir (caso
    de um wheel instalado sem as fontes), a auditoria de código simplesmente
    não roda, em vez de reportar "nenhum achado" sobre algo que não olhou.
    """

    def __init__(
        self,
        target_dir: Path | None = None,
        source_root: Path | None = None,
        scan_source: bool = True,
    ) -> None:
        self.target_dir = target_dir or Path.cwd()
        self.scan_source = scan_source
        self.source_root = source_root or (find_source_root() if scan_source else None)

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

        # 7. Código-fonte (SAST) — o repositório, não a instalação.
        if self.scan_source and self.source_root is not None:
            findings.extend(scan_source_tree(self.source_root))

        duration = (time.time() - t0) * 1000
        return SecurityReport(
            target=str(self.target_dir),
            source_root=str(self.source_root) if self.source_root else None,
            findings=findings,
            duration_ms=round(duration, 2),
        )


def run_full_audit(
    target_dir: Path | None = None,
    source_root: Path | None = None,
    scan_source: bool = True,
) -> SecurityReport:
    auditor = SecurityAuditor(target_dir, source_root=source_root, scan_source=scan_source)
    return auditor.run_audit()
