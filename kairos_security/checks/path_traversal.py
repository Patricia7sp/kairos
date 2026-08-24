"""Auditoria de proteção contra Path Traversal em ferramentas de arquivos."""

from __future__ import annotations

from pathlib import Path

from kairos_security.models import Category, Finding, Severity


def check_path_traversal_guards(workspace_root: Path, target_path_str: str) -> bool:
    """Verifica se um caminho alvo tenta escapar do diretório raiz permitido."""
    try:
        root_resolved = workspace_root.resolve()
        target_resolved = (workspace_root / target_path_str).resolve()
        return root_resolved in target_resolved.parents or target_resolved == root_resolved
    except Exception:  # noqa: BLE001
        return False


def run_path_traversal_tests(workspace: Path) -> list[Finding]:
    findings: list[Finding] = []
    escape_payloads = [
        "../../etc/passwd",
        "../.ssh/id_rsa",
        "/etc/shadow",
        "../../root/.bashrc",
    ]

    for payload in escape_payloads:
        is_confined = check_path_traversal_guards(workspace, payload)
        if is_confined:
            findings.append(
                Finding(
                    id="SEC-TRAVERSAL-001",
                    title="Falha no isolamento de caminho (Path Traversal)",
                    description=f"O payload de escape {payload!r} foi incorretamente aceito como seguro dentro de {workspace}.",
                    severity=Severity.HIGH,
                    category=Category.PATH_TRAVERSAL,
                    remediation="Utilize `Path.resolve()` comparando com o diretório raiz permitido.",
                    evidence=f"Payload aceito: {payload}",
                )
            )

    return findings
