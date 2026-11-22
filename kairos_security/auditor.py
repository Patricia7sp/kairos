"""Orquestrador do motor de auditoria de segurança (SecurityAuditor)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from kairos_security.checks.agent_guardrails import check_agent_policy_invariants
from kairos_security.checks.api_surface import check_api_security_configuration
from kairos_security.checks.path_traversal import run_path_traversal_tests
from kairos_security.checks.permissions import check_file_permissions
from kairos_security.checks.prompt_injection import evaluate_prompt_hardening
from kairos_security.checks.secrets import check_hardcoded_secrets
from kairos_security.checks.source_code import scan_source_tree
from kairos_security.models import (
    AuditProfile,
    CheckError,
    CheckResult,
    CheckState,
    Finding,
    SecurityReport,
)


def _check_api() -> list[Finding]:
    from kairos_web.server import app

    cors = next((m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"), None)
    origins = cors.kwargs.get("allow_origins", []) if cors is not None else []
    return check_api_security_configuration(origins)


def _available_directory(path: Path | None) -> bool:
    try:
        return path is not None and path.is_dir()
    except OSError:
        return False


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
        if not scan_source:
            self.source_root = None
        elif source_root is not None:
            self.source_root = source_root
        else:
            try:
                self.source_root = find_source_root()
            except (OSError, RuntimeError):
                self.source_root = None

    def run_audit(self, system_prompt: str = "") -> SecurityReport:
        t0 = time.monotonic()
        findings: list[Finding] = []
        checks: list[CheckResult] = []

        def collect(
            check_id: str,
            run: Callable[[Callable[[], None]], list[Finding]],
            *,
            available: bool,
        ) -> None:
            if not available:
                checks.append(
                    CheckResult(check_id, CheckState.UNAVAILABLE, CheckError.TARGET_UNAVAILABLE)
                )
                return
            read_error = False

            def mark_read_error() -> None:
                nonlocal read_error
                read_error = True

            try:
                collected = run(mark_read_error)
            except ImportError:
                checks.append(
                    CheckResult(check_id, CheckState.UNAVAILABLE, CheckError.DETECTOR_UNAVAILABLE)
                )
            except Exception:  # noqa: BLE001 - erro isolado no detector; texto bruto não sai
                checks.append(CheckResult(check_id, CheckState.FAILED, CheckError.COLLECTION_ERROR))
            else:
                findings.extend(
                    replace(f, detector=check_id, detector_version=1) for f in collected
                )
                state = CheckState.PARTIAL if read_error else CheckState.COMPLETED
                checks.append(
                    CheckResult(check_id, state, CheckError.READ_ERROR if read_error else None)
                )

        runtime = _available_directory(self.target_dir)
        collect("permissions", lambda _: check_file_permissions(self.target_dir), available=runtime)
        collect(
            "secrets",
            lambda mark: check_hardcoded_secrets(self.target_dir, on_error=mark),
            available=runtime,
        )
        collect(
            "path_traversal", lambda _: run_path_traversal_tests(self.target_dir), available=runtime
        )
        collect("agent_guardrails", lambda _: check_agent_policy_invariants(), available=runtime)
        collect("api_surface", lambda _: _check_api(), available=runtime)
        default_prompt = system_prompt or "Você é o Kairos, um assistente agentic de programação."
        collect(
            "prompt_injection",
            lambda _: evaluate_prompt_hardening(default_prompt),
            available=runtime,
        )
        if self.scan_source:
            source = self.source_root
            if source is None:
                checks.append(
                    CheckResult(
                        "source_code", CheckState.UNAVAILABLE, CheckError.TARGET_UNAVAILABLE
                    )
                )
            else:
                collect(
                    "source_code",
                    lambda mark: scan_source_tree(source, on_error=mark),
                    available=_available_directory(source),
                )

        duration = (time.monotonic() - t0) * 1000
        return SecurityReport(
            target=str(self.target_dir),
            source_root=str(self.source_root) if self.source_root else None,
            findings=findings,
            duration_ms=round(duration, 2),
            checks=tuple(checks),
            profile=AuditProfile.LOCAL_FULL if self.scan_source else AuditProfile.LOCAL_RUNTIME,
        )


def run_full_audit(
    target_dir: Path | None = None,
    source_root: Path | None = None,
    scan_source: bool = True,
) -> SecurityReport:
    auditor = SecurityAuditor(target_dir, source_root=source_root, scan_source=scan_source)
    return auditor.run_audit()
