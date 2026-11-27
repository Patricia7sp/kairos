"""Despacho interno de provas e exceções; dados de relatório não são credenciais."""

import hashlib
import json
import queue
import re
import secrets
import sqlite3
from collections import Counter

from kairos_security import snapshot as scanner
from kairos_security.checks.source_code import RULES
from kairos_security.models import AuditProfile, CheckState, Severity
from kairos_security.policy import (
    AUDIT_POLICY_VERSION,
    PROFILE_VERSION,
    ExecutionState,
    _assess_audit,
    assess_audit,
)
from kairos_tools.audit_store import AuthorityDenied, Clock, require
from kairos_tools.authorization import (
    APPROVE_EXCEPTION,
    READ_SNAPSHOT,
    REVOKE_EXCEPTION,
    current_grants,
)

REASONS = {
    "verified_false_positive": "Falso positivo verificado",
    "temporary_risk_external_mitigation": "Risco temporariamente aceito com mitigação externa",
}
TRANSPORT = "private_https"
PROOF_TTL = EXCEPTION_TTL = 24 * 60 * 60
INTENT_TTL = 10 * 60
RETENTION = 30 * PROOF_TTL
MAX_ROWS = 4096
MAX_PROOF_BYTES = 64 * 1024
MAX_TOTAL_PROOF_BYTES = 16 * 1024 * 1024
FINDING_KEY_VERSION = 1
_RULES = {
    "snapshot_secrets": {"SEC-SECRET-001"},
    "snapshot_sast": {*(r.id for r in RULES), "SEC-SRC-011"},
}


class CapacityExceeded(Exception):
    pass


def _ref(prefix):
    return prefix + secrets.token_hex(32)


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _clock_valid(now, row, issued, issued_boot, *, expires, deadline):
    return now.within(row["boot_id"], row[issued], row[issued_boot], expires, deadline)


def _within_ttl(now, row, issued, issued_boot, ttl):
    return _clock_valid(
        now,
        row,
        issued,
        issued_boot,
        expires=row[issued] + ttl,
        deadline=row[issued_boot] + ttl * 10**9,
    )


def _proof_current(now, proof):
    return _within_ttl(now, proof, "created_at", "created_boot_ns", PROOF_TTL)


def _janitor(db, now):
    # A prova é a raiz da trilha. Um salto no relógio não elimina dados do boot corrente.
    db.execute(
        """DELETE FROM audit_proofs WHERE created_at<=?
        AND (boot_id!=? OR created_boot_ns<=?)""",
        (now.utc - RETENTION, now.generation, now.boot_ns - RETENTION * 10**9),
    )
    db.execute(
        """DELETE FROM audit_intents WHERE consumed_at IS NULL AND
        (boot_id!=? OR (issued_at<=? AND deadline_boot_ns<=?))""",
        (now.generation, now.utc - INTENT_TTL, now.boot_ns),
    )


def _reserve(db, *, rows=1, payload=""):
    count, size = db.execute(
        """SELECT count(*) + (SELECT count(*) FROM audit_intents)
        + (SELECT count(*) FROM audit_exceptions) + (SELECT count(*) FROM audit_assessments),
        coalesce(sum(length(projection)),0) FROM audit_proofs"""
    ).fetchone()
    if count + rows > MAX_ROWS or len(payload) > min(MAX_PROOF_BYTES, MAX_TOTAL_PROOF_BYTES - size):
        raise CapacityExceeded()


def _descriptor(snapshot, finding):
    detector = finding.detector if finding.detector in _RULES else None
    rule = finding.id if detector and finding.id in _RULES[detector] else None
    location = finding.file_path
    if not isinstance(location, str) or not re.fullmatch(r"arquivo:[a-f0-9]{64}", location):
        location = None
    version = finding.detector_version
    if type(version) is not int or not 0 < version < 2**31:
        version = None
    info = {
        "rule": rule,
        "severity": Severity(finding.severity).value,
        "detector": detector,
        "detector_version": version,
        "location": location,
    }
    key = None
    if all(info.values()):
        canonical = _json(
            [
                FINDING_KEY_VERSION,
                snapshot.project,
                snapshot.revision,
                snapshot.export_identity,
                snapshot.fingerprint,
                AuditProfile.SNAPSHOT_STATIC,
                PROFILE_VERSION,
                detector,
                version,
                info["severity"],
                finding.fingerprint,
            ]
        )
        key = hashlib.sha256(canonical.encode("ascii")).hexdigest()
    return info, key


def _project(snapshot, report, assessment):
    checks = [
        {"check_id": c.check_id, "state": c.state, "error": c.error} for c in assessment.checks
    ]
    valid_inventory = (
        report.profile is AuditProfile.SNAPSHOT_STATIC
        and (report.target, report.target_revision) == (snapshot.project, snapshot.revision)
        and assessment.execution_state in (ExecutionState.COMPLETED, ExecutionState.PARTIAL)
        and any(
            c["check_id"] == "snapshot_inventory" and c["state"] == CheckState.COMPLETED
            for c in checks
        )
    )
    descriptors = [_descriptor(snapshot, f) for f in report.findings]
    counts = Counter(key for _, key in descriptors)
    complete = {c["check_id"] for c in checks if c["state"] == CheckState.COMPLETED}
    eligible, findings = {}, []
    for i, (info, key) in enumerate(descriptors):
        if valid_inventory and key and counts[key] == 1 and info["detector"] in complete:
            info.update(finding_ref=_ref("finding_"), key=key)
            eligible[key] = i
        findings.append(info)
    return (
        valid_inventory,
        eligible,
        {
            "execution_state": assessment.execution_state,
            "checks": checks,
            "findings": findings,
            "counts": report.counts_by_severity,
            "security_score": report.security_score,
        },
    )


def _public_finding(finding, *, selectable=True):
    public = {
        k: finding[k] for k in ("rule", "severity", "detector", "detector_version", "location")
    }
    if selectable and "finding_ref" in finding:
        public["finding_ref"] = finding["finding_ref"]
    return public


def _public_proof(scope, projection, run_id, *, profile_version, selectable=True):
    project, revision = scope
    return {
        "project": project,
        "revision": revision,
        "run_id": run_id,
        "profile": AuditProfile.SNAPSHOT_STATIC,
        "profile_version": profile_version,
        "execution_state": projection["execution_state"],
        "checks": projection["checks"],
        "counts": projection["counts"],
        "security_score": projection["security_score"],
        "findings": [
            _public_finding(f, selectable=selectable and run_id is not None)
            for f in projection["findings"]
        ],
    }


def _grant(db, now, principal, project, revision, *, action, ref=None):
    return next(current_grants(db, now, principal, project, revision, action=action, ref=ref), None)


def _snapshot_access(db, now, principal, snapshot, ref):
    read = _grant(
        db, now, principal, snapshot.project, snapshot.revision, action=READ_SNAPSHOT, ref=ref
    )
    if read is None or (read["root_identity"], read["fingerprint"]) != (
        snapshot.export_identity,
        snapshot.fingerprint,
    ):
        raise AuthorityDenied()
    return read


def _proof_grant(db, now, principal, proof, action, *, ref=None):
    grant = _grant(db, now, principal, proof["project"], proof["revision"], action=action, ref=ref)
    if grant is not None and (grant["root_identity"], grant["fingerprint"]) == (
        proof["export_identity"],
        proof["fingerprint"],
    ):
        return grant
    return None


def _live_exception(db, now, decision, proof):
    principal = decision["principal"]
    return (
        decision["revoked_at"] is None
        and decision["policy_version"] == AUDIT_POLICY_VERSION
        and _clock_valid(
            now,
            decision,
            "issued_at",
            "issued_boot_ns",
            expires=decision["expires_at"],
            deadline=decision["expires_boot_ns"],
        )
        and _proof_grant(db, now, principal, proof, READ_SNAPSHOT, ref=decision["read_grant"])
        is not None
        and _proof_grant(
            db, now, principal, proof, APPROVE_EXCEPTION, ref=decision["approve_grant"]
        )
        is not None
    )


def _matching(db, principal, proof, epoch, *, now, eligible):
    matches = {}
    for row in db.execute(
        """SELECT x.* FROM audit_exceptions x JOIN audit_proofs p ON p.id=x.proof_run_id
        WHERE x.principal=? AND x.policy_epoch=? AND x.revoked_at IS NULL AND x.expires_at>?
        AND p.project=? AND p.revision=? AND p.export_identity=? AND p.fingerprint=?
        AND p.profile=? AND p.profile_version=? ORDER BY x.id""",
        (
            principal,
            epoch,
            now.utc,
            proof["project"],
            proof["revision"],
            proof["export_identity"],
            proof["fingerprint"],
            AuditProfile.SNAPSHOT_STATIC,
            PROFILE_VERSION,
        ),
    ):
        key = row["finding_key"]
        if key in eligible and _live_exception(db, now, row, proof):
            matches[key] = row["id"] if key not in matches else None
    return {eligible[key]: ref for key, ref in matches.items() if ref is not None}


def _evaluate(db, now, *, principal, snapshot, grant, report, baseline, eligible, payload):
    read = _snapshot_access(db, now, principal, snapshot, grant)
    run_id, assessment_id = _ref("run_"), _ref("assessment_")
    proof = {
        "project": snapshot.project,
        "revision": snapshot.revision,
        "export_identity": snapshot.export_identity,
        "fingerprint": snapshot.fingerprint,
    }
    matches = (
        _matching(db, principal, proof, read["epoch"], now=now, eligible=eligible)
        if eligible
        else {}
    )
    assessed = _assess_audit(report, validated_indices=frozenset(matches)) if matches else baseline
    applied = sorted(matches.values())
    db.execute(
        "INSERT INTO audit_proofs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            run_id,
            principal,
            grant,
            snapshot.project,
            snapshot.revision,
            snapshot.export_identity,
            snapshot.fingerprint,
            AuditProfile.SNAPSHOT_STATIC,
            PROFILE_VERSION,
            read["epoch"],
            now.utc,
            now.boot_ns,
            now.generation,
            payload,
        ),
    )
    db.execute(
        "INSERT INTO audit_assessments VALUES(?,?,?,?,?,?,?,?,1)",
        (
            assessment_id,
            run_id,
            assessed.policy_outcome,
            assessed.coverage_complete,
            assessed.severity_threshold,
            _json(applied),
            now.utc,
            now.boot_ns,
        ),
    )
    return run_id, assessment_id, assessed, applied


def observe(authority, principal, *, project, revision, grant):
    """Captura e executa o auditor aqui: não há entrada de relatório para registrar prova."""
    inbox = queue.Queue(maxsize=1)
    authority.capture(principal, project, revision, grant, inbox)
    snapshot = inbox.get_nowait()
    report = scanner.audit_snapshot(snapshot)
    baseline = assess_audit(report)
    valid, eligible, projection = _project(snapshot, report, baseline)
    committed = None
    if valid:
        try:
            payload = _json(projection)
            with authority.connection(write=True) as db:
                _janitor(db, Clock.read())
                _reserve(db, rows=2, payload=payload)
                now = Clock.read()
                result = _evaluate(
                    db,
                    now,
                    principal=principal,
                    snapshot=snapshot,
                    grant=grant,
                    report=report,
                    baseline=baseline,
                    eligible=eligible,
                    payload=payload,
                )
            committed = (now, result)
        except AuthorityDenied:
            raise
        except (sqlite3.Error, OSError, CapacityExceeded):
            pass
    if committed is None:
        with authority.connection() as db:
            now = Clock.read()
            _snapshot_access(db, now, principal, snapshot, grant)
        run_id, evaluation_id, assessed, applied = None, None, baseline, []
    else:
        now, (run_id, evaluation_id, assessed, applied) = committed
    return _public_proof(
        (project, revision), projection, run_id, profile_version=PROFILE_VERSION
    ) | {
        "assessment_id": evaluation_id,
        "evaluated_at": now.utc,
        "exceptions_available": committed is not None,
        "policy_outcome": assessed.policy_outcome,
        "coverage_complete": assessed.coverage_complete,
        "threshold": assessed.severity_threshold,
        "applied_exception_ids": applied,
    }


def _proof(db, now, principal, run_id):
    row = db.execute(
        "SELECT * FROM audit_proofs WHERE id=? AND principal=?", (run_id, principal)
    ).fetchone()
    if row is None:
        raise AuthorityDenied()
    require(
        now.utc - row["created_at"] < RETENTION
        or (
            now.generation == row["boot_id"]
            and now.boot_ns - row["created_boot_ns"] < RETENTION * 10**9
        )
    )
    read = _proof_grant(db, now, principal, row, READ_SNAPSHOT, ref=row["read_grant"])
    if read is None or read["epoch"] != row["policy_epoch"]:
        raise AuthorityDenied()
    return row, read


def open_proof(authority, principal, *, run_id):
    with authority.connection() as db:
        now = Clock.read()
        proof, _ = _proof(db, now, principal, run_id)
        history = db.execute(
            "SELECT * FROM audit_assessments WHERE run_id=? AND commit_confirmed=1", (run_id,)
        ).fetchone()
        if history is None:
            raise AuthorityDenied()
        selectable = _proof_current(now, proof) and proof["profile_version"] == PROFILE_VERSION
        projection = _public_proof(
            (proof["project"], proof["revision"]),
            json.loads(proof["projection"]),
            run_id,
            profile_version=proof["profile_version"],
            selectable=selectable,
        )
        return projection | {
            "historical": True,
            "assessment_id": history["id"],
            "evaluated_at": history["evaluated_at"],
            "policy_outcome": history["policy_outcome"],
            "coverage_complete": bool(history["coverage_complete"]),
            "threshold": history["threshold"],
            "applied_exception_ids": json.loads(history["applied_ids"]),
        }


def _requested_deadline(now, expires, read, approve):
    require(now.utc < expires <= min(now.utc + EXCEPTION_TTL, read["expires"], approve["expires"]))
    deadline = now.deadline(expires)
    require(now.boot_ns < deadline)
    # Uma diferença entre os relógios não pode estender a exceção além dos grants.
    return min(deadline, read["deadline"], approve["deadline"])


def _approve_grant(db, now, principal, proof, ref=None):
    row = _proof_grant(db, now, principal, proof, APPROVE_EXCEPTION, ref=ref)
    if row is None or row["epoch"] != proof["policy_epoch"]:
        raise AuthorityDenied()
    return row


def prepare(authority, principal, *, origin, run_id, finding_ref, reason_code, expires_at):
    require(reason_code in REASONS)
    with authority.connection(write=True) as db:
        _janitor(db, Clock.read())
        _reserve(db)
        now = Clock.read()
        proof, read = _proof(db, now, principal, run_id)
        require(_proof_current(now, proof) and proof["profile_version"] == PROFILE_VERSION)
        findings = json.loads(proof["projection"])["findings"]
        selected = [f for f in findings if f.get("finding_ref") == finding_ref and "key" in f]
        require(len(selected) == 1)
        finding = selected[0]
        approve = _approve_grant(db, now, principal, proof)
        _requested_deadline(now, expires_at, read, approve)
        intent_id = _ref("intent_")
        db.execute(
            "INSERT INTO audit_intents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (
                intent_id,
                run_id,
                APPROVE_EXCEPTION,
                principal,
                finding["key"],
                approve["id"],
                origin,
                TRANSPORT,
                proof["policy_epoch"],
                AUDIT_POLICY_VERSION,
                reason_code,
                expires_at,
                now.utc,
                now.boot_ns,
                now.boot_ns + INTENT_TTL * 10**9,
                now.generation,
            ),
        )
    return {
        "intent_ref": intent_id,
        "action": APPROVE_EXCEPTION,
        "principal": principal,
        "attribution": "Instância autenticada; credencial compartilhada não identifica uma pessoa.",
        "project": proof["project"],
        "revision": proof["revision"],
        "profile": proof["profile"],
        "profile_version": proof["profile_version"],
        "policy_version": AUDIT_POLICY_VERSION,
        "finding": _public_finding(finding),
        "reason_code": reason_code,
        "reason": REASONS[reason_code],
        "expires_at": expires_at,
        "confirm_before": min(expires_at, now.utc + INTENT_TTL, proof["created_at"] + PROOF_TTL),
    }


def _status(decision, now):
    if decision["revoked_at"] is not None:
        state = "revoked"
    elif _clock_valid(
        now,
        decision,
        "issued_at",
        "issued_boot_ns",
        expires=decision["expires_at"],
        deadline=decision["expires_boot_ns"],
    ):
        state = "confirmed"
    else:
        state = "expired"
    return {"exception_id": decision["id"], "state": state, "expires_at": decision["expires_at"]}


def confirm(authority, principal, *, origin, intent_ref):
    with authority.connection(write=True) as db:
        now = Clock.read()
        intent = db.execute(
            "SELECT * FROM audit_intents WHERE id=? AND principal=?", (intent_ref, principal)
        ).fetchone()
        if intent is None:
            raise AuthorityDenied()
        require(
            intent["action"] == APPROVE_EXCEPTION
            and intent["origin"] == origin
            and intent["transport"] == TRANSPORT
            and intent["policy_version"] == AUDIT_POLICY_VERSION
        )
        proof, read = _proof(db, now, principal, intent["run_id"])
        require(
            intent["policy_epoch"] == proof["policy_epoch"]
            and proof["profile_version"] == PROFILE_VERSION
        )
        approve = _approve_grant(db, now, principal, proof, intent["approve_grant"])
        if intent["consumed_at"] is not None:
            decision = db.execute(
                "SELECT * FROM audit_exceptions WHERE intent_id=?", (intent_ref,)
            ).fetchone()
            if decision is None:
                raise AuthorityDenied()
            return _status(decision, now)
        require(
            _proof_current(now, proof)
            and _within_ttl(now, intent, "issued_at", "issued_boot_ns", INTENT_TTL)
            and now.boot_ns < intent["deadline_boot_ns"]
        )
        _janitor(db, now)
        _reserve(db)
        expires = intent["expires_at"]
        deadline = _requested_deadline(now, expires, read, approve)
        # Prazo monotônico vencido, reboot, mudança de epoch ou revogação são terminais.
        # Uma divergência temporária de relógio/grant não permite emitir uma duplicata.
        require(
            db.execute(
                """SELECT 1 FROM audit_exceptions WHERE principal=? AND finding_key=? AND policy_epoch=?
            AND revoked_at IS NULL AND boot_id=? AND expires_boot_ns>? LIMIT 1""",
                (
                    principal,
                    intent["finding_key"],
                    proof["policy_epoch"],
                    now.generation,
                    now.boot_ns,
                ),
            ).fetchone()
            is None
        )
        exception_id = _ref("exception_")
        db.execute(
            "INSERT INTO audit_exceptions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL)",
            (
                exception_id,
                intent_ref,
                proof["id"],
                APPROVE_EXCEPTION,
                principal,
                intent["finding_key"],
                approve["id"],
                proof["read_grant"],
                proof["policy_epoch"],
                AUDIT_POLICY_VERSION,
                intent["reason_code"],
                now.utc,
                now.boot_ns,
                now.generation,
                expires,
                deadline,
            ),
        )
        require(
            db.execute(
                "UPDATE audit_intents SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now.utc, intent_ref),
            ).rowcount
            == 1
        )
    return {"exception_id": exception_id, "state": "confirmed", "expires_at": expires}


def revoke(authority, principal, *, exception_id):
    with authority.connection(write=True) as db:
        decision = db.execute(
            """SELECT x.*,p.project,p.revision,p.export_identity,p.fingerprint
            FROM audit_exceptions x JOIN audit_proofs p ON p.id=x.proof_run_id WHERE x.id=?""",
            (exception_id,),
        ).fetchone()
        if decision is None:
            raise AuthorityDenied()
        now = Clock.read()
        grant = _proof_grant(db, now, principal, decision, REVOKE_EXCEPTION)
        if grant is None:
            raise AuthorityDenied()
        db.execute(
            """UPDATE audit_exceptions SET revoked_at=?,revoked_by=?,revoke_grant=?,revocation_reason=?
            WHERE id=? AND revoked_at IS NULL""",
            (now.utc, principal, grant["id"], "operator_revoked", exception_id),
        )
    return {"exception_id": exception_id, "state": "revoked"}
