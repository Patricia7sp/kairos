import fcntl
import json
import multiprocessing
import shutil
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from test_security_snapshot import MARKER, ORIGIN, SECRET, register

from kairos_security import audit_exceptions as exceptions
from kairos_security import snapshot as scanner
from kairos_security.models import Severity
from kairos_tools import audit_store, authorization
from kairos_tools.audit_store import DB, LOCK, AuthorityDenied, Clock
from kairos_tools.authorization import APPROVE_EXCEPTION, REVOKE_EXCEPTION, AuditAuthority
from kairos_web.audit_authority import dispatch_audit_exception

DENIED = {"error": "Auditoria não autorizada."}
OTHER_ORIGIN = "https://alternate.example.test"
PRINCIPAL = "web:operador"
FILES = {f"{MARKER}.json": f'{{"value":"{SECRET}"}}'.encode(), "ok.py": b"answer = 42\n"}


def post(authority, action, data, *, origin=ORIGIN, **options):
    from kairos_web.server import SESSION_COOKIE, SESSION_TOKEN

    async def handle(request):
        try:
            result = await dispatch_audit_exception(
                request, authority=authority, origin=origin, action=action
            )
        except AuthorityDenied as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        return JSONResponse(result)

    app = Starlette(routes=[Route("/internal", handle, methods=["POST", "GET"])])
    with TestClient(app, base_url=origin) as client:
        headers = {"Origin": origin, "Content-Type": "application/json"} | options.pop(
            "headers", {}
        )
        if options.pop("session", True):
            client.cookies.set(SESSION_COOKIE, SESSION_TOKEN)
        if isinstance(data, dict):
            data = json.dumps(data)
        response = client.request(
            options.pop("method", "POST"),
            options.pop("url", "/internal"),
            content=data,
            headers=headers,
            **options,
        )
        assert response.status_code in (200, 403), response.text
        return response.json()


class Harness:
    def __init__(self, tmp_path, files=FILES):
        self.authority, self.target, self.workspace = register(tmp_path, files)

    def call(self, action, data=None, **options):
        return post(self.authority, action, self.target if data is None else data, **options)

    def grant(self, action, *, seconds=3600):
        return self.authority.grant(
            PRINCIPAL,
            self.target["project"],
            self.target["revision"],
            expires=int(Clock.read().utc) + seconds,
            action=action,
        )

    def prepare_data(self, proof, *, seconds=90):
        return {
            "run_id": proof["run_id"],
            "finding_ref": proof["findings"][0]["finding_ref"],
            "reason_code": "verified_false_positive",
            "expires_at": int(Clock.read().utc) + seconds,
        }

    def approved(self, *, seconds=90):
        self.grant(APPROVE_EXCEPTION)
        proof = self.call("observe")
        intent = self.call("prepare", self.prepare_data(proof, seconds=seconds))
        return proof, intent, self.call("confirm", {"intent_ref": intent["intent_ref"]})

    def rows(self, table):
        query = {
            "audit_proofs": "SELECT * FROM audit_proofs",
            "audit_intents": "SELECT * FROM audit_intents",
            "audit_exceptions": "SELECT * FROM audit_exceptions",
            "audit_assessments": "SELECT * FROM audit_assessments",
            "grants": "SELECT * FROM grants",
            "projects": "SELECT * FROM projects",
        }[table]
        with self.authority.connection() as db:
            return [dict(row) for row in db.execute(query)]


@pytest.fixture
def audit(tmp_path):
    return Harness(tmp_path)


def test_decisao_rastreavel_e_projecao_sanitizada(audit, caplog):
    baseline, intent, decision = audit.approved()
    current = audit.call("observe")
    history = audit.call("open_proof", {"run_id": current["run_id"]})
    assert baseline["policy_outcome"] == "fail" and current["policy_outcome"] == "pass"
    assert current["run_id"] != baseline["run_id"] and current["coverage_complete"]
    assert current["applied_exception_ids"] == [decision["exception_id"]]
    assert (history["assessment_id"], history["policy_outcome"], history["evaluated_at"]) == (
        current["assessment_id"],
        "pass",
        current["evaluated_at"],
    )
    assert (
        baseline["counts"] == current["counts"]
        and baseline["security_score"] == current["security_score"]
    )
    before, after = (result["findings"][0] for result in (baseline, current))
    assert before.pop("finding_ref") != after.pop("finding_ref") and before == after
    assert before["severity"] == "critical" and before["location"].startswith("arquivo:")
    assert (
        intent["finding"]["location"] == before["location"]
        and intent["expires_at"] == decision["expires_at"]
    )
    assert intent["principal"] == PRINCIPAL and "não identifica uma pessoa" in intent["attribution"]
    (stored,) = audit.rows("audit_exceptions")
    assessments = audit.rows("audit_assessments")
    assert (
        stored["proof_run_id"] == baseline["run_id"] and stored["intent_id"] == intent["intent_ref"]
    )
    assert (
        stored["read_grant"] == audit.target["grant"]
        and stored["expires_at"] == intent["expires_at"]
    )
    persisted = next(r for r in assessments if r["id"] == current["assessment_id"])
    assert (
        persisted["commit_confirmed"] == 1
        and json.loads(persisted["applied_ids"]) == current["applied_exception_ids"]
    )
    public = json.dumps([baseline, intent, decision, current, history])
    assert stored["finding_key"] not in public and stored["read_grant"] not in public
    private = json.dumps(
        [audit.rows("audit_proofs"), audit.rows("audit_intents"), stored, assessments]
    )
    for value in (SECRET, SECRET[:16], MARKER, str(audit.workspace)):
        assert value not in public and value not in private and value not in caplog.text


def test_despacho_exige_cookie_origem_schema_e_grants_exatos(audit):
    from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER

    proof = audit.call("observe")
    data = audit.prepare_data(proof)
    assert audit.call("prepare", data) == DENIED  # grant de leitura não aprova
    audit.grant(REVOKE_EXCEPTION)
    assert audit.call("prepare", data) == DENIED  # grant de revogação também não aprova
    approval = audit.grant(APPROVE_EXCEPTION)
    assert audit.call("observe", audit.target | {"grant": approval}) == DENIED
    assert audit.call("observe", audit.target | {"principal": PRINCIPAL}) == DENIED
    for body in (
        data | {"fingerprint": "a" * 64},
        data | {"report": proof},
        data | {"expires_at": True},
        data | {"reason_code": []},
        data | {"finding_ref": "finding_" + "a" * 64},
        data | {"reason_code": MARKER},
        b'{"run_id":1,"run_id":2}',
        b" " * 1025,
    ):
        assert audit.call("prepare", body) == DENIED
    for options in (
        {"session": False, "headers": {TOKEN_HEADER: SESSION_TOKEN}},
        {"session": False, "headers": {"Authorization": "Bearer " + SESSION_TOKEN}},
        {"headers": {"Origin": OTHER_ORIGIN}},
        {"headers": {"Content-Type": "application/json; charset=utf-8"}},
        {"method": "GET"},
        {"url": "/internal?transport=wss"},
    ):
        assert audit.call("prepare", data, **options) == DENIED
    intent = audit.call("prepare", data)
    selection = {"intent_ref": intent["intent_ref"]}
    assert audit.call("confirm", selection | {"expires_at": data["expires_at"] + 1}) == DENIED
    assert audit.call("confirm", selection, origin=OTHER_ORIGIN) == DENIED
    assert audit.rows("audit_exceptions") == []
    assert audit.call("open_proof", {"run_id": proof["run_id"]})["run_id"] == proof["run_id"]
    audit.authority.revoke(audit.target["grant"])
    assert audit.call("confirm", selection) == DENIED
    assert (
        audit.call("open_proof", {"run_id": proof["run_id"]})
        == audit.call("open_proof", {"run_id": "run_" + "a" * 64})
        == DENIED
    )
    assert audit.rows("audit_exceptions") == []


@pytest.mark.parametrize(
    "change", ["detector_version", "severity", "duplicate", "profile_version", "policy_version"]
)
def test_chave_inequivoca_e_versionada(audit, monkeypatch, change):
    _, _, _ = audit.approved()
    if change == "detector_version":
        monkeypatch.setitem(
            scanner.DETECTOR_VERSIONS,
            "snapshot_secrets",
            scanner.DETECTOR_VERSIONS["snapshot_secrets"] + 1,
        )
    elif change in ("profile_version", "policy_version"):
        variable = "PROFILE_VERSION" if change == "profile_version" else "AUDIT_POLICY_VERSION"
        monkeypatch.setattr(exceptions, variable, getattr(exceptions, variable) + 1)
    else:
        original = scanner.audit_snapshot

        def collect(snapshot):
            report = original(snapshot)
            (f,) = report.findings
            if change == "duplicate":
                report.findings.append(f)
            else:
                report.findings[0] = replace(f, severity=Severity.HIGH)
            return report

        monkeypatch.setattr(scanner, "audit_snapshot", collect)
    result = audit.call("observe")
    assert result["policy_outcome"] == "fail" and result["applied_exception_ids"] == []
    if change == "duplicate":
        assert len(result["findings"]) == 2 and all(
            "finding_ref" not in f for f in result["findings"]
        )


@pytest.mark.parametrize("change", ["project", "revision", "export"])
def test_mesmo_achado_em_outra_exportacao_continua_bloqueando(audit, tmp_path, change):
    baseline, _, _ = audit.approved()
    authority, previous = audit.authority, audit.target
    revision = previous["revision"]
    catalog = audit.workspace.parents[1]
    project = previous["project"]
    if change == "project":
        catalog = shutil.copytree(catalog, tmp_path / "other-catalog")
        project = authority.register(catalog, revision)
    elif change == "revision":
        new_revision = "b" * 40
        copy = shutil.copytree(catalog / revision, catalog / new_revision)
        manifest = copy / "project.json"
        manifest.write_text(
            json.dumps(json.loads(manifest.read_text()) | {"revision": new_revision})
        )
        revision = new_revision
        assert authority.register(catalog, revision) == project
    else:
        # Simula recadastro por autoridade local com nova raiz, mesma revisão e fingerprint.
        detached = audit.workspace.with_name("original")
        audit.workspace.rename(detached)
        shutil.copytree(detached, audit.workspace)
        with authority.connection(write=True) as db:
            import os

            fd = os.open(audit.workspace, os.O_DIRECTORY)
            try:
                db.execute(
                    "UPDATE exports SET root_identity=? WHERE project=?",
                    (authorization._identity(fd), project),
                )
            finally:
                os.close(fd)
    read = authority.grant(PRINCIPAL, project, revision, expires=int(time.time()) + 60)
    current = audit.call("observe", {"project": project, "revision": revision, "grant": read})
    assert current["counts"] == baseline["counts"] and current["applied_exception_ids"] == []
    assert current["policy_outcome"] == "fail"


@pytest.mark.parametrize(("producer", "outcome"), [(True, "fail"), (False, "unknown")])
def test_cobertura_parcial_e_falha_do_produtor(audit, monkeypatch, caplog, producer, outcome):
    _, _, decision = audit.approved()

    def broken(*_args, **_kwargs):
        raise RuntimeError(f"{MARKER} {SECRET}")

    if producer:
        original = scanner.scan_secret_text

        def fail_one(path, *args, **kwargs):
            return broken() if path.suffix == ".py" else original(path, *args, **kwargs)

        monkeypatch.setattr(scanner, "scan_secret_text", fail_one)
    else:
        monkeypatch.setattr(scanner, "scan_source_text", broken)
    current = audit.call("observe")
    assert current["policy_outcome"] == outcome and current["execution_state"] == "partial"
    assert not current["coverage_complete"] and current["applied_exception_ids"] == (
        [] if producer else [decision["exception_id"]]
    )
    assert ("finding_ref" not in current["findings"][0]) == producer
    assert MARKER not in (str(current) + caplog.text) and SECRET not in str(
        audit.rows("audit_proofs")
    )


def test_resta_outro_achado_bloqueante(tmp_path):
    audit = Harness(tmp_path, FILES | {"second.json": f'{{"other":"{SECRET}"}}'.encode()})
    before, _, decision = audit.approved()
    after = audit.call("observe")
    assert before["policy_outcome"] == after["policy_outcome"] == "fail"
    assert len(after["findings"]) == len(before["findings"]) == 2 and after["coverage_complete"]
    assert after["applied_exception_ids"] == [decision["exception_id"]]


@pytest.mark.parametrize("change", ["read", "approve", "epoch", "retired"])
def test_revalidacao_dos_grants_originais(audit, change):
    audit.approved()
    ref = audit.rows("audit_exceptions")[0]
    if change == "retired":
        audit.authority.retire(audit.target["project"])
        assert audit.call("observe") == DENIED
        return
    if change == "epoch":
        audit.authority.invalidate_policy()
    else:
        audit.authority.revoke(ref[change + "_grant"])
    audit.target["grant"] = audit.grant("read_snapshot")
    audit.grant(APPROVE_EXCEPTION)
    assert audit.call("observe")["policy_outcome"] == "fail"


@pytest.fixture
def clock(monkeypatch):
    fixed = SimpleNamespace(utc=time.time(), boot=audit_store.boot_seconds())
    monkeypatch.setattr(audit_store, "time", SimpleNamespace(time=lambda: fixed.utc))
    monkeypatch.setattr(audit_store, "boot_seconds", lambda: fixed.boot)
    return fixed


def test_prazos_intencao_e_excecao(audit, clock):
    audit.target["grant"] = audit.grant("read_snapshot")
    _, intent, decision = audit.approved(seconds=15)
    clock.utc += 16
    clock.boot += 16
    assert audit.call("observe")["policy_outcome"] == "fail"
    assert audit.call("confirm", {"intent_ref": intent["intent_ref"]}) == decision | {
        "state": "expired"
    }
    proof = audit.call("observe")
    unconsumed = audit.call("prepare", audit.prepare_data(proof))
    clock.utc += exceptions.INTENT_TTL
    clock.boot += exceptions.INTENT_TTL
    assert audit.call("confirm", {"intent_ref": unconsumed["intent_ref"]}) == DENIED
    assert len(audit.rows("audit_exceptions")) == 1


def test_prova_antiga_pode_ser_lida_mas_nao_confirma(audit, clock):
    audit.target["grant"] = audit.grant("read_snapshot", seconds=3 * exceptions.PROOF_TTL)
    audit.grant(APPROVE_EXCEPTION, seconds=3 * exceptions.PROOF_TTL)
    proof = audit.call("observe")
    clock.utc += exceptions.PROOF_TTL - 10
    clock.boot += exceptions.PROOF_TTL - 10
    data = audit.prepare_data(proof)
    intent = audit.call("prepare", data)
    assert intent["confirm_before"] == proof["evaluated_at"] + exceptions.PROOF_TTL
    clock.utc += 10
    clock.boot += 10
    assert audit.call("confirm", {"intent_ref": intent["intent_ref"]}) == DENIED
    assert audit.call("prepare", data) == DENIED
    history = audit.call("open_proof", {"run_id": proof["run_id"]})
    assert history["historical"] and "finding_ref" not in history["findings"][0]
    assert audit.rows("audit_exceptions") == []


@pytest.mark.parametrize("defect", ["cancelled", "inventory", "read_revoked"])
def test_prova_exige_inventario_execucao_e_leitura_valida_apos_coleta(audit, monkeypatch, defect):
    from kairos_security.models import CheckError, CheckResult, CheckState

    original = scanner.audit_snapshot

    def collect(snapshot):
        report = original(snapshot)
        if defect == "cancelled":
            report.cancelled = True
        elif defect == "inventory":
            report.checks = (
                CheckResult("snapshot_inventory", CheckState.FAILED, CheckError.INVALID_RESULT),
                *report.checks[1:],
            )
        else:
            audit.authority.revoke(audit.target["grant"])
        return report

    monkeypatch.setattr(scanner, "audit_snapshot", collect)
    result = audit.call("observe")
    if defect == "read_revoked":
        assert result == DENIED
    else:
        assert result["run_id"] is None and result["policy_outcome"] == "fail"
        assert "finding_ref" not in result["findings"][0]
    assert audit.rows("audit_proofs") == audit.rows("audit_assessments") == []


@pytest.mark.parametrize("change", ["wall_jump", "reboot"])
def test_relogio_incompativel_nao_entrega_nem_reaplica(audit, clock, monkeypatch, change):
    _, intent, _ = audit.approved()
    if change == "wall_jump":
        clock.utc += 30
    else:
        monkeypatch.setattr(audit_store, "boot_id", lambda: "new-boot")
    assert audit.call("observe") == DENIED
    assert audit.call("confirm", {"intent_ref": intent["intent_ref"]}) == DENIED
    assert len(audit.rows("audit_assessments")) == 1


def test_confirmacao_repetida_recupera_commit_de_resposta_perdida(audit, monkeypatch):
    proof = audit.call("observe")
    audit.grant(APPROVE_EXCEPTION)
    intent = audit.call("prepare", audit.prepare_data(proof))
    handle = {"intent_ref": intent["intent_ref"]}
    real = audit.authority.connection

    @contextmanager
    def uncertain(**kw):
        with real(**kw) as db:
            yield db
        raise sqlite3.OperationalError("resposta à gravação perdida")

    with monkeypatch.context() as failure:
        failure.setattr(audit.authority, "connection", uncertain)
        assert audit.call("confirm", handle) == DENIED
    reopened = AuditAuthority(audit.authority.home)
    confirmed = post(reopened, "confirm", handle)
    assert post(reopened, "confirm", handle) == confirmed
    (stored,) = audit.rows("audit_exceptions")
    assert confirmed == {
        "exception_id": stored["id"],
        "expires_at": intent["expires_at"],
        "state": "confirmed",
    }


def test_commit_falho_e_teto_usam_politica_sem_excecao(audit, monkeypatch):
    audit.approved()
    real = audit.authority.connection

    @contextmanager
    def reject_commit(*, write=False):
        def authorizer(action, operation, *_):
            return (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION and operation == "COMMIT"
                else sqlite3.SQLITE_OK
            )

        with real(write=write) as db:
            if write:
                db.set_authorizer(authorizer)
            yield db

    with monkeypatch.context() as failure:
        failure.setattr(audit.authority, "connection", reject_commit)
        failed = audit.call("observe")
    monkeypatch.setattr(exceptions, "MAX_ROWS", 4)
    full = audit.call("observe")
    for result in (failed, full):
        assert result["policy_outcome"] == "fail" and result["applied_exception_ids"] == []
        assert (
            result["run_id"] is None
            and result["assessment_id"] is None
            and not result["exceptions_available"]
        )
    assert len(audit.rows("audit_assessments")) == len(audit.rows("audit_exceptions")) == 1


def test_nao_publica_avaliacao_quando_confirmacao_do_commit_se_perde(audit, monkeypatch):
    _, _, decision = audit.approved()
    real = audit.authority.connection

    @contextmanager
    def uncertain(*, write=False):
        with real(write=write) as db:
            yield db
        if write:
            raise sqlite3.OperationalError("confirmação do commit perdida")

    with monkeypatch.context() as failure:
        failure.setattr(audit.authority, "connection", uncertain)
        result = audit.call("observe")
    assert result["policy_outcome"] == "fail" and result["applied_exception_ids"] == []
    assert not result["exceptions_available"] and result["run_id"] is None
    committed = next(r for r in audit.rows("audit_assessments") if r["policy_outcome"] == "pass")
    assert json.loads(committed["applied_ids"]) == [decision["exception_id"]]


def test_restore_invalida_autoridade_antes_de_reabrir(audit):
    _, intent, decision = audit.approved()
    audit.grant(REVOKE_EXCEPTION)
    reference = {"exception_id": decision["exception_id"]}
    path = audit.authority.home / DB
    backup = path.with_suffix(".backup")
    shutil.copyfile(path, backup)
    backup.chmod(0o600)
    assert audit.call("revoke", reference)["state"] == "revoked"
    backup.replace(path)
    assert audit.call("observe") == DENIED and audit.call("revoke", reference) == DENIED
    audit.authority.reset_after_restore()
    assert audit.rows("grants") == [] and audit.rows("projects") == []
    assert audit.rows("audit_exceptions")[0]["revocation_reason"] == "authority_restored"
    assert audit.call("confirm", {"intent_ref": intent["intent_ref"]}) == DENIED
    assert audit.call("observe") == DENIED
    new_project = audit.authority.register(audit.workspace.parents[1], audit.target["revision"])
    assert new_project != audit.target["project"]
    audit.target["project"] = new_project
    audit.target["grant"] = audit.grant("read_snapshot")
    assert audit.call("observe")["policy_outcome"] == "fail"


def test_migracao_explicita_v1_preserva_catalogo_e_invalida_grants(audit, tmp_path):
    home = tmp_path / "legacy"
    home.mkdir(mode=0o700)
    lock, path = home / LOCK, home / DB
    lock.touch(mode=0o600)
    path.touch(mode=0o600)
    from contextlib import closing

    with audit.authority.connection() as source, closing(sqlite3.connect(path)) as db, db:
        db.executescript(audit_store.SCHEMA + "PRAGMA user_version=1;")
        db.execute(
            "INSERT INTO binding VALUES(?,?,?,?)",
            (lock.stat().st_dev, lock.stat().st_ino, path.stat().st_dev, path.stat().st_ino),
        )
        db.execute(
            "INSERT INTO projects VALUES(?,?,?)",
            tuple(source.execute("SELECT * FROM projects").fetchone()),
        )
        db.execute(
            "INSERT INTO exports VALUES(?,?,?,?)",
            tuple(source.execute("SELECT * FROM exports").fetchone()),
        )
        db.execute(
            "INSERT INTO grants VALUES(?,?,?,?,'read_snapshot',1,?,?,?,0)",
            (
                audit.target["grant"],
                PRINCIPAL,
                audit.target["project"],
                audit.target["revision"],
                time.time() + 3600,
                time.time(),
                3600,
            ),
        )
    audit.authority = AuditAuthority(home)
    assert audit.call("observe") == DENIED
    audit.authority.migrate()
    assert audit.call("observe") == DENIED and audit.rows("grants") == []
    audit.target["grant"] = audit.grant("read_snapshot")
    assert audit.call("observe")["policy_outcome"] == "fail"


def _revoke_process(home, target, start, ready, done, *, must_wait):
    assert start.wait(10)
    original = fcntl.flock

    def verify(fd, mode):
        if must_wait:
            with pytest.raises(BlockingIOError):
                original(fd, mode | fcntl.LOCK_NB)
        ready.set()
        return original(fd, mode)

    fcntl.flock = verify
    assert post(AuditAuthority(home), "revoke", target)["state"] == "revoked"
    done.set()


@pytest.mark.parametrize("evaluation_first", [False, True])
def test_ordena_revogacao_e_avaliacao_em_processos_distintos(audit, monkeypatch, evaluation_first):
    _, _, decision = audit.approved()
    audit.grant(REVOKE_EXCEPTION)
    target = {"exception_id": decision["exception_id"]}
    mp = multiprocessing.get_context("spawn")
    start, waiting, done = mp.Event(), mp.Event(), mp.Event()
    process = mp.Process(
        target=_revoke_process,
        args=(audit.authority.home, target, start, waiting, done),
        kwargs={"must_wait": evaluation_first},
    )
    if evaluation_first:
        real = exceptions._evaluate

        def evaluate(*args, **kw):
            result = real(*args, **kw)
            start.set()
            assert waiting.wait(10), "revogação não tentou adquirir o lock"
            assert not done.is_set()
            return result

        monkeypatch.setattr(exceptions, "_evaluate", evaluate)
    else:
        real_scan = scanner.audit_snapshot

        def audit_after_revoke(snapshot):
            result = real_scan(snapshot)
            start.set()
            assert done.wait(10), "captura/detecção não pode reter o lock da decisão"
            return result

        monkeypatch.setattr(scanner, "audit_snapshot", audit_after_revoke)
    process.start()
    try:
        result = audit.call("observe")
        assert done.wait(10)
    finally:
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
        assert process.exitcode == 0
    monkeypatch.undo()
    assert result["policy_outcome"] == ("pass" if evaluation_first else "fail")
    assert result["applied_exception_ids"] == (
        [decision["exception_id"]] if evaluation_first else []
    )
    historical = audit.call("open_proof", {"run_id": result["run_id"]})
    assert historical["policy_outcome"] == result["policy_outcome"]
    assert audit.call("observe")["policy_outcome"] == "fail"
    revoked_at = audit.rows("audit_exceptions")[0]["revoked_at"]
    audit.authority.revoke(audit.target["grant"])
    assert audit.call("revoke", target) == target | {"state": "revoked"}
    assert audit.rows("audit_exceptions")[0]["revoked_at"] == revoked_at


def _confirm_process(home, intent, ready, results):
    ready.wait(10)
    results.put(post(AuditAuthority(home), "confirm", {"intent_ref": intent}))


def test_duas_intencoes_concorrentes_so_uma_decisao(audit):
    audit.grant(APPROVE_EXCEPTION)
    data = audit.prepare_data(audit.call("observe"))
    intents = [audit.call("prepare", data)["intent_ref"] for _ in range(2)]
    mp = multiprocessing.get_context("spawn")
    ready, results = mp.Barrier(3), mp.Queue()
    processes = [
        mp.Process(target=_confirm_process, args=(audit.authority.home, i, ready, results))
        for i in intents
    ]
    for process in processes:
        process.start()
    try:
        ready.wait(10)
        replies = [results.get(timeout=10) for _ in processes]
    finally:
        for process in processes:
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join()
            assert process.exitcode == 0
    assert replies.count(DENIED) == 1
    (stored,) = audit.rows("audit_exceptions")
    assert audit.call("observe")["applied_exception_ids"] == [stored["id"]]
