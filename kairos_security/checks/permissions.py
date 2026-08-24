"""Auditoria de permissões em arquivos sensíveis e credenciais."""

from __future__ import annotations

import os
from pathlib import Path

from kairos_security.models import Category, Finding, Severity


def check_file_permissions(target_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    auth_file = target_dir / "auth.json"

    if auth_file.exists():
        mode = auth_file.stat().st_mode & 0o777
        if os.name != "nt" and (mode & 0o077) != 0:
            findings.append(
                Finding(
                    id="SEC-PERM-001",
                    title="Permissões inseguras no arquivo de credenciais auth.json",
                    description=f"O arquivo {auth_file} possui permissões {oct(mode)}, permitindo acesso a outros usuários.",
                    severity=Severity.HIGH,
                    category=Category.PERMISSIONS,
                    file_path=str(auth_file),
                    remediation=f"Execute: chmod 0600 {auth_file}",
                    evidence=f"Modo atual: {oct(mode)} | Esperado: 0o600",
                )
            )

    return findings
