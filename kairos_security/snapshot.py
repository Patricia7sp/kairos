"""Auditoria estática da cópia privada entregue pela autoridade de leitura."""

from __future__ import annotations

import hashlib
import re
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import PurePosixPath

from kairos_filesystem.contract import FilesystemError, TreeCapture
from kairos_filesystem.tree import validate_capture
from kairos_runtime.projects import validate_export_path
from kairos_runtime.reviews import fingerprint_files
from kairos_security.checks.secrets import is_secret_candidate, scan_secret_text
from kairos_security.checks.source_code import is_source_candidate, scan_source_text
from kairos_security.models import (
    AuditProfile,
    CheckError,
    CheckResult,
    CheckState,
    Finding,
    SecurityReport,
)
from kairos_tools.authorization import LIMITS, Snapshot

_SCOPE = (
    "Somente arquivos da exportação autorizada; o exportador recusa entradas como .env*.",
    "Segredos e SAST cobrem apenas os arquivos elegíveis às regras do perfil.",
    "Localizações usam identificadores de arquivo; caminhos e conteúdo bruto são omitidos.",
)
DETECTOR_VERSIONS = {"snapshot_secrets": 1, "snapshot_sast": 1}


def _identity(snapshot: Snapshot) -> tuple[str, str]:
    project = uuid.UUID(snapshot.project)
    if (
        project.version != 4
        or str(project) != snapshot.project
        or not re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", snapshot.revision)
    ):
        raise ValueError("Identidade de exportação inválida.")
    return str(project), snapshot.revision


def _inventory(snapshot: Snapshot) -> dict[str, bytes]:
    # Copia as referências aos bytes (imutáveis), não reabre a origem.
    if len(snapshot.entries) > LIMITS.entries or len(snapshot.contents) > LIMITS.entries:
        raise ValueError("Inventário excede o limite.")
    contents = dict(snapshot.contents)
    validate_capture(TreeCapture(snapshot.entries, contents), limits=LIMITS, require_contents=True)
    files = {}
    for entry in snapshot.entries:
        if entry.path:
            validate_export_path(entry.path)
        if entry.kind == "file":
            mode = 0o755 if entry.mode & 0o111 else 0o644
            files[entry.path] = (contents[entry.path], mode)
    if fingerprint_files(files) != snapshot.fingerprint:
        raise ValueError("Fingerprint da exportação divergente.")
    return contents


def _scan(
    files: dict[str, bytes],
    check_id: str,
    include: Callable[[PurePosixPath], bool],
    detect: Callable[[PurePosixPath, str, str], list[Finding]],
) -> tuple[CheckResult, list[Finding]]:
    findings: list[Finding] = []
    error = None
    for path, data in files.items():
        relative = PurePosixPath(path)
        if not include(relative):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if error is None:
                error = CheckError.READ_ERROR
            continue
        location = "arquivo:" + hashlib.sha256(path.encode("utf-8")).hexdigest()
        try:
            collected = detect(relative, text, location)
        except Exception:  # noqa: BLE001 - falha de um detector não descarta os achados já observados
            error = CheckError.COLLECTION_ERROR
            continue
        findings.extend(
            replace(f, detector=check_id, detector_version=DETECTOR_VERSIONS[check_id])
            for f in collected
        )
    state = CheckState.PARTIAL if error is not None else CheckState.COMPLETED
    return CheckResult(check_id, state, error), findings


def audit_snapshot(snapshot: Snapshot) -> SecurityReport:
    """Consome somente a entrega interna da autoridade; não recebe caminhos do caller."""
    started = time.monotonic()
    report = SecurityReport(
        "(exportação não atestada)", profile=AuditProfile.SNAPSHOT_STATIC, scope_notes=_SCOPE
    )
    try:
        report.target, report.target_revision = _identity(snapshot)
        files = _inventory(snapshot)
    except (FilesystemError, ValueError, TypeError, AttributeError):
        report.checks = (
            CheckResult("snapshot_inventory", CheckState.FAILED, CheckError.INVALID_RESULT),
        )
    else:
        checks = [CheckResult("snapshot_inventory", CheckState.COMPLETED)]
        for check_id, include, detector in (
            (
                "snapshot_secrets",
                is_secret_candidate,
                lambda path, text, location: scan_secret_text(path, text, location=location),
            ),
            (
                "snapshot_sast",
                is_source_candidate,
                lambda _path, text, location: scan_source_text(text, location=location),
            ),
        ):
            check, findings = _scan(files, check_id, include, detector)
            checks.append(check)
            report.findings.extend(findings)
        report.checks = tuple(checks)
    report.duration_ms = round((time.monotonic() - started) * 1000, 2)
    return report
