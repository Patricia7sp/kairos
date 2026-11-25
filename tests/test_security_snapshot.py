import asyncio
import json
import os
import queue
import subprocess
import time
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from starlette.requests import Request

from kairos_runtime.projects import export_project
from kairos_security import format_cli_summary, format_json_report
from kairos_security import snapshot as scanner
from kairos_security.models import CheckError, CheckState
from kairos_security.policy import assess_audit
from kairos_tools.authorization import LIMITS, AuditAuthority, AuthorityDenied
from kairos_web.audit_authority import dispatch_snapshot_audit

ORIGIN = "https://kairos.example.test"
MARKER = "CONTEUDO_PRIVADO_SINTETICO"
SECRET = "sk-ant-" + "Q7w_" * 12


def export(tmp_path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name, content in files.items():
        (repo / name).write_bytes(content)
    git = ["git", "-C", str(repo)]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run(
        [
            *git,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    return export_project(repo, "HEAD", tmp_path / "catalog")


def register(tmp_path, files):
    published = export(tmp_path, files)
    home = tmp_path / "private"
    home.mkdir(mode=0o700)
    authority = AuditAuthority(home)
    authority.initialize()
    revision = published["revision"]
    project = authority.register(tmp_path / "catalog", revision)
    grant = authority.grant("web:operador", project, revision, expires=int(time.time()) + 120)
    return (
        authority,
        {"project": project, "revision": revision, "grant": grant},
        Path(published["cwd"]),
    )


def request(selectors):
    from kairos_web.server import SESSION_COOKIE, SESSION_TOKEN

    headers = [
        (b"origin", ORIGIN.encode()),
        (b"content-type", b"application/json"),
        (b"cookie", f"{SESSION_COOKIE}={SESSION_TOKEN}".encode()),
    ]

    async def receive():
        return {"type": "http.request", "body": json.dumps(selectors).encode()}

    return Request({"type": "http", "method": "POST", "headers": headers}, receive)


def test_despacho_audita_copia_apos_remover_origem_sem_expor_dados(tmp_path, monkeypatch):
    path = f"{MARKER}.py"
    text = f'API_KEY = "{SECRET}"\ntry:\n    go()\nexcept Exception:  # {MARKER}\n    pass\n'
    authority, selectors, workspace = register(tmp_path, {path: text.encode()})
    inspect = scanner.audit_snapshot

    def audit_after_removal(snapshot):
        (workspace / path).unlink()
        assert SECRET not in repr(snapshot) and MARKER not in repr(snapshot)

        def deny_io(*_args, **_kwargs):
            pytest.fail("o auditor reabriu o filesystem")

        with monkeypatch.context() as no_io:
            no_io.setattr(Path, "open", deny_io)
            no_io.setattr(os, "open", deny_io)
            no_io.setattr(os, "scandir", deny_io)
            return inspect(snapshot)

    monkeypatch.setattr(scanner, "audit_snapshot", audit_after_removal)
    report = asyncio.run(
        dispatch_snapshot_audit(request(selectors), authority=authority, origin=ORIGIN)
    )
    result = assess_audit(report)
    assert result.execution_state == "completed" and result.policy_outcome == "fail"
    assert report.target == selectors["project"] and report.target_revision == selectors["revision"]
    assert {(f.id, f.detector, f.line_number) for f in report.findings} >= {
        ("SEC-SECRET-001", "snapshot_secrets", 1),
        ("SEC-SRC-002", "snapshot_sast", 1),
        ("SEC-SRC-011", "snapshot_sast", 4),
    }
    assert len({f.file_path for f in report.findings}) == 1
    for output in (
        format_json_report(report),
        format_cli_summary(report),
        repr(report),
        json.dumps(asdict(report)),
    ):
        assert all(value not in output for value in (SECRET, SECRET[:16], MARKER, str(workspace)))


@pytest.mark.parametrize(("secret", "outcome"), [(False, "unknown"), (True, "fail")])
def test_snapshot_com_falha_de_decodificacao_conserva_cobertura_e_politica(
    tmp_path, secret, outcome
):
    files = {"invalid.py": b"\xff", "valid.py": b"answer = 42\n"}
    if secret:
        files["valid.py"] = f'API_KEY = "{SECRET}"\n'.encode()
    authority, selectors, _ = register(tmp_path, files)
    report = asyncio.run(
        dispatch_snapshot_audit(request(selectors), authority=authority, origin=ORIGIN)
    )
    result = assess_audit(report)
    assert result.execution_state == "partial" and not result.coverage_complete
    assert result.policy_outcome == outcome
    assert {(c.check_id, c.error) for c in result.checks if c.state is CheckState.PARTIAL} == {
        ("snapshot_secrets", CheckError.READ_ERROR),
        ("snapshot_sast", CheckError.READ_ERROR),
    }


def test_captura_invalida_nao_chama_detectores(tmp_path, monkeypatch):
    authority, selectors, _ = register(tmp_path, {"valid.py": b"answer = 42\n"})
    inbox = queue.Queue(maxsize=1)
    authority.capture("web:operador", **selectors, inbox=inbox)
    snapshot = inbox.get_nowait()
    assert scanner.audit_snapshot(snapshot).passed

    def deny_scan(*_args, **_kwargs):
        pytest.fail("detector recebeu dados não validados")

    monkeypatch.setattr(scanner, "scan_secret_text", deny_scan)
    monkeypatch.setattr(scanner, "scan_source_text", deny_scan)
    for modified in (
        replace(snapshot, contents={}),
        replace(snapshot, fingerprint="0" * 64),
        replace(
            snapshot,
            entries=(snapshot.entries[0], replace(snapshot.entries[1], size=LIMITS.file_bytes + 1)),
        ),
    ):
        result = assess_audit(scanner.audit_snapshot(modified))
        assert result.policy_outcome == "unknown" and result.execution_state == "failed"
        assert result.checks[0].error is CheckError.INVALID_RESULT


def test_grant_revogado_nao_chega_ao_auditor(tmp_path, monkeypatch):
    authority, selectors, _ = register(tmp_path, {"valid.py": b"answer = 42\n"})
    authority.revoke(selectors["grant"])
    monkeypatch.setattr(scanner, "audit_snapshot", lambda _: pytest.fail("auditoria sem grant"))
    with pytest.raises(AuthorityDenied):
        asyncio.run(dispatch_snapshot_audit(request(selectors), authority=authority, origin=ORIGIN))


@pytest.mark.parametrize("entry", ["link_simbolico", "link_fisico", "ausente"])
def test_exportacao_alterada_nao_chega_ao_auditor(tmp_path, monkeypatch, entry):
    authority, selectors, workspace = register(tmp_path, {"valid.py": b"answer = 42\n"})
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"answer = 42\n")
    original = workspace / "valid.py"
    original.unlink()
    if entry == "link_simbolico":
        original.symlink_to(outside)
    elif entry == "link_fisico":
        original.hardlink_to(outside)

    monkeypatch.setattr(
        scanner, "audit_snapshot", lambda _: pytest.fail("auditoria de origem divergente")
    )
    with pytest.raises(AuthorityDenied):
        asyncio.run(dispatch_snapshot_audit(request(selectors), authority=authority, origin=ORIGIN))


@pytest.mark.parametrize("name", [".env", ".env.local"])
def test_exportacao_recusa_arquivo_env_rastreado(tmp_path, name):
    with pytest.raises(ValueError, match="excluded by snapshot policy"):
        export(tmp_path, {name: b"EXEMPLO=valor\n"})
