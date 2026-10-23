"""Matriz reutilizável de SIGKILL, com arquivos e journal reais."""

import json
import multiprocessing
import os
import shutil
import signal
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from unittest.mock import patch

from kairos_domain.ownership import Actor
from kairos_skills.acquisitions import SkillAcquisitionService
from kairos_skills.hub import SkillBundle
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_contract import ManifestDelta
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_quarantine_source
from kairos_skills.publications import SkillPublicationService
from kairos_skills.removal_io import capture_skill

NAME = "personal-skill"
PHASES = (
    "before_prepare",
    "before_prepare_commit",
    "after_prepare",
    "before_publish",
    "after_publish",
    "before_parent_sync",
    "after_parent_sync",
    "before_commit",
    "before_commit_commit",
    "after_commit",
    "before_manifest_write",
    "after_manifest_write",
    "before_manifest_confirmation",
    "after_manifest_confirmation",
    "before_delete_source",
    "after_delete_source",
    "before_cleanup_confirmation",
    "after_cleanup_confirmation",
)


def capture(path):
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return replace(capture_skill(fd, path.name), contents=None)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class Case:
    home: Path
    quarantine: Path
    policy_file: Path
    source_key: str
    acquisition_id: str
    previous: object

    @property
    def target(self):
        return self.home / "skills" / NAME

    @property
    def source(self):
        return self.quarantine / self.acquisition_id / NAME


@contextmanager
def opened(case):
    with ExitStack() as stack:
        policy = stack.enter_context(
            observe_org_policy(OrgPolicyContext("file", case.policy_file), case.home)
        )
        context = stack.enter_context(
            observe_quarantine_source(
                case.home,
                case.quarantine,
                source_key=case.source_key,
                actor=Actor.USER_FOREGROUND,
                policy=policy,
                replacement=case.previous,
            )
        )
        session = stack.enter_context(
            publication_session(case.home, policy=policy, context=context)
        )
        yield session, SkillPublicationService(session)


def create_case(root, quarantine_root, *, replacement, cache_assets=False):
    root.mkdir(mode=0o700)
    home = root / "home"
    home.mkdir(mode=0o700)
    quarantine = quarantine_root / uuid.uuid4().hex
    quarantine.mkdir(mode=0o700)
    policy = root / "policy.json"
    policy.write_text('{"version":1,"bindings":[]}')
    previous = None
    if replacement:
        target = home / "skills" / NAME
        target.mkdir(parents=True, mode=0o700)
        (target / "SKILL.md").write_bytes(b"previous version")
        if cache_assets:
            (target / "__pycache__").mkdir(mode=0o700)
            (target / "__pycache__/required.bin").write_bytes(b"previous required asset")
            (target / "__pycache__/required.bin").chmod(0o600)
        previous = capture(target)
    case = Case(home, quarantine, policy, uuid.uuid4().hex, "", previous)
    assets = {"SKILL.md": b"new version", "asset.txt": b"asset"}
    if cache_assets:
        assets[".cache/required.bin"] = b"new required asset"
    with opened(case) as (session, _service):
        receipt = SkillAcquisitionService(session).acquire(SkillBundle(NAME, assets))
    return replace(case, acquisition_id=receipt.acquisition_id)


def publish_until(case, phase, pipe):
    from kairos_skills import publications, sync

    with opened(case) as (session, service), ExitStack() as patches:
        operation_id = None
        transaction = None
        trace = []

        def pause(observed):
            trace.append(observed)
            if phase == observed:
                pipe.send({"phase": observed, "operation_id": operation_id})
                # Somente SIGKILL termina esta pausa; o pai exige o sinal antes de matar.
                while True:
                    signal.pause()

        original_stage = session.files.stage

        def stage(identifier, *args, **kwargs):
            nonlocal operation_id, transaction
            operation_id = identifier
            transaction = "stage"
            try:
                return original_stage(identifier, *args, **kwargs)
            finally:
                transaction = None

        patches.enter_context(patch.object(session.files, "stage", side_effect=stage))

        def boundary(owner, method, label):
            original = getattr(owner, method)

            def call(*args, **kwargs):
                nonlocal transaction
                pause("before_" + label)
                old_transaction = transaction
                transaction = label
                try:
                    result = original(*args, **kwargs)
                finally:
                    transaction = old_transaction
                pause("after_" + label)
                return result

            patches.enter_context(patch.object(owner, method, side_effect=call))

        for owner, method, label in (
            (session.publications, "prepare", "prepare"),
            (session.publications, "finish", "commit"),
            (session.files, "retire", "retire"),
            (session.files, "publish", "publish"),
            (sync, "reconcile_manifest", "manifest_write"),
            (session.publications, "confirm_manifest", "manifest_confirmation"),
            (publications, "delete_publication", "delete_source"),
            (session.publications, "confirm_cleanup", "cleanup_confirmation"),
        ):
            boundary(owner, method, label)
        install_durability_boundaries(patches, session, pause, lambda: transaction)
        result = service.publish(
            NAME,
            acquisition_id=case.acquisition_id,
            manifest_delta=ManifestDelta(NAME, None, "a" * 32),
        )
        pipe.send({"unexpected_completion": result.complete, "phase": phase, "trace": trace})


def install_durability_boundaries(patches, session, pause, transaction_label):
    from kairos_skills import publication_files

    syncing = False
    fsync_indices = {}
    original_write = session.publications._write

    def write(operation, guard):
        def check():
            guard()
            pause("before_" + str(transaction_label()) + "_commit")

        return original_write(operation, check)

    patches.enter_context(patch.object(session.publications, "_write", side_effect=write))
    original_sync = session.files.sync_parents
    original_fsync = os.fsync

    def fsync(fd):
        label = "parent" if syncing else transaction_label()
        observed = label in ("parent", "stage", "manifest_write", "delete_source")
        index = fsync_indices.get(label, 0)
        if observed:
            fsync_indices[label] = index + 1
            pause(f"before_{label}_fsync_{index}")
        original_fsync(fd)
        if observed:
            pause(f"after_{label}_fsync_{index}")

    def sync_parents(*args, **kwargs):
        nonlocal syncing
        pause("before_parent_sync")
        syncing = True
        try:
            result = original_sync(*args, **kwargs)
        finally:
            syncing = False
        pause("after_parent_sync")
        return result

    patches.enter_context(patch.object(publication_files.os, "fsync", side_effect=fsync))
    patches.enter_context(patch.object(session.files, "sync_parents", side_effect=sync_parents))


def kill_at(case, phase, *, mutation=None):
    context = multiprocessing.get_context("fork")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=publish_until, args=(case, phase, child))
    try:
        process.start()
        child.close()
        if not parent.poll(25):
            raise AssertionError(f"Fronteira não sinalizada: {phase}; exit={process.exitcode}")
        reached = parent.recv()
        if reached.get("phase") != phase or "operation_id" not in reached:
            raise AssertionError(reached)
        if mutation is not None:
            mutation(case)
        if not process.is_alive():
            raise AssertionError("Filho encerrou antes de SIGKILL")
        process.kill()
        process.join(10)
        if process.exitcode != -signal.SIGKILL:
            raise AssertionError(f"SIGKILL não comprovado: {process.exitcode}")
        return reached["operation_id"]
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
        parent.close()
        child.close()


def assert_no_new_writer(case, service, operation_id):
    try:
        service.publish(NAME, acquisition_id=case.acquisition_id)
    except SkillMutationError as error:
        assert error.operation_id == operation_id, "Escritor deve informar ID bloqueador"
    else:
        raise AssertionError("Escritor concorrente atravessou pendência")


def events(session):
    return tuple(
        tuple(row)
        for row in session.connection.execute(
            "SELECT * FROM skill_publication_events ORDER BY sequence"
        )
    )


def artifact_inventory(path):
    return tuple(
        (
            str(p.relative_to(path)),
            p.stat().st_ino,
            p.stat().st_mode,
            p.read_bytes() if p.is_file() else None,
        )
        for p in (path, *sorted(path.rglob("*")))
    )


def recover_case(case, operation_id, phase):
    before_prepare = phase in ("before_prepare", "before_prepare_commit") or "stage_fsync" in phase
    before_install = before_prepare or phase in (
        "after_prepare",
        "before_retire",
        "after_retire",
        "before_publish",
    )
    cleanup_gap = (
        phase in ("after_delete_source", "before_cleanup_confirmation")
        or "delete_source_fsync" in phase
    )
    with opened(case) as (session, service):
        assert session.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        record = session.publications.get(operation_id)
        if before_prepare:
            assert record is None, "PREPARED sem commit não pode sobreviver"
            staged = case.home / ".skill-publications" / operation_id
            original = artifact_inventory(staged)
            try:
                service.recover(operation_id)
            except SkillMutationError:
                pass
            else:
                raise AssertionError("Órfão desconhecido adotado")
            assert artifact_inventory(staged) == original, "Órfão foi alterado"
        else:
            assert record is not None, "ID durável perdido"
            if record.pending:
                assert_no_new_writer(case, service, operation_id)
            first = service.recover(operation_id)
            canonical = events(session)
            second = service.recover(operation_id)
            assert first == second and second.operation_id == operation_id
            assert events(session) == canonical, "Repetição acrescentou história"
            record = session.publications.get(operation_id)
            expected = (
                SkillMutationState.ABORTED if before_install else SkillMutationState.COMMITTED
            )
            assert record.state is expected and record.pending == cleanup_gap, (
                phase,
                record.state,
                record.pending,
            )
            if not before_install:
                assert capture(case.target) == record.draft.staged_tree
                assert session.files.capture_previous(operation_id) == case.previous
                assert record.manifest_complete
        if before_install:
            assert (
                (capture(case.target) == case.previous)
                if case.previous
                else not case.target.exists()
            )
            assert case.source.exists(), "Fonte apagada no aborto"
        return record


def run_matrix(root, quarantine_root, *, phases=PHASES):
    started = time.monotonic()
    count = 0
    for replacement in (False, True):
        selected = tuple(phases)
        if phases is PHASES:
            selected += durability_phases(root, quarantine_root, replacement=replacement)
        selected += ("before_retire", "after_retire") if replacement else ()
        for phase in selected:
            case = create_case(root / f"case-{count}", quarantine_root, replacement=replacement)
            operation_id = kill_at(case, phase)
            recover_case(case, operation_id, phase)
            count += 1
    return {"sigkills": count, "seconds": round(time.monotonic() - started, 2)}


def durability_phases(root, quarantine_root, *, replacement):
    class Trace:
        def send(self, value):
            self.result = value

    case = create_case(
        root / ("trace-replacement" if replacement else "trace-initial"),
        quarantine_root,
        replacement=replacement,
    )
    trace = Trace()
    publish_until(case, None, trace)
    assert trace.result["unexpected_completion"], "Publicação de controle deve concluir"
    phases = tuple(value for value in trace.result["trace"] if "_fsync_" in value)
    for label in ("stage", "parent", "manifest_write", "delete_source"):
        assert any(value.startswith("before_" + label + "_fsync_") for value in phases), label
    return phases


def run_divergences(root, quarantine_root):
    def protect(case):
        case.policy_file.write_text(
            json.dumps(
                {
                    "version": 1,
                    "bindings": [
                        {
                            "home": str(case.home),
                            "name": NAME,
                            "organization_id": "test-org",
                            "active": False,
                        }
                    ],
                }
            )
        )

    def change_root(case):
        preserved = case.target.with_name("preserved-version")
        case.target.rename(preserved)
        shutil.copytree(preserved, case.target)

    for index, mutation in enumerate(
        (protect, lambda case: shutil.rmtree(case.source), change_root)
    ):
        case = create_case(root / f"divergence-{index}", quarantine_root, replacement=True)
        operation_id = kill_at(case, "after_publish", mutation=mutation)
        with opened(case) as (session, service):
            before = capture(case.target)
            previous = session.files.capture_previous(operation_id)
            result = service.recover(operation_id)
            assert not result.complete
            assert session.publications.get(operation_id).pending
            assert capture(case.target) == before
            assert session.files.capture_previous(operation_id) == previous == case.previous
            assert not service.recover(operation_id).complete
            try:
                service.publish(NAME, acquisition_id=case.acquisition_id)
            except SkillMutationError:
                pass
            else:
                raise AssertionError("Outro escritor atravessou divergência pendente")
    return {"divergences": 3}
