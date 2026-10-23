"""Aceite offline contra módulos instalados; somente este harness é montado."""

import argparse
import io
import json
import os
import sqlite3
import sys
import tarfile
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))

from skill_publication_process import (
    NAME,
    capture,
    create_case,
    kill_at,
    opened,
    run_divergences,
    run_matrix,
)

import kairos_skills
from kairos_cli.backup import run_backup
from kairos_skills.identification import identify_skill
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_backup import validate_restored_publication
from kairos_state.migrations import CANONICAL_TABLES


def canonical(home):
    with closing(sqlite3.connect(home / "state.db")) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        return {
            name: tuple(tuple(row) for row in connection.execute('SELECT * FROM "' + name + '"'))  # noqa: S608 — nomes fixos do schema observado.
            for name in sorted(names & CANONICAL_TABLES)
        }


def restore(archive, destination, *, database_only=False):
    destination.mkdir(mode=0o700)
    with tarfile.open(archive) as tar:
        members = [tar.getmember("state.db")] if database_only else tar.getmembers()
        tar.extractall(destination, members=members, filter="data")
        for member in members:
            safe = tarfile.data_filter(member, str(destination))
            if safe is not None and (safe.isdir() or safe.isfile()):
                (destination / safe.name).chmod(member.mode & 0o777)


def run_backup_acceptance(root, quarantine_root):
    case = create_case(root / "backup-case", quarantine_root, replacement=True, cache_assets=True)
    operation_id = kill_at(case, "after_commit")
    with opened(case) as (session, _service):
        record = session.publications.get(operation_id)
        assert record.pending and not record.manifest_complete and not record.cleanup_complete
    before = canonical(case.home)
    archive = root / "backup.tar.gz"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = run_backup(case.home, argparse.Namespace(output=str(archive), json=True))
    assert code == 0, code
    for database_only in (False, True):
        destination = root / ("database-only" if database_only else "full-restore")
        restore(archive, destination, database_only=database_only)
        assert canonical(destination) == before
        with observe_org_policy(OrgPolicyContext("none", None), destination) as policy:
            observation = validate_restored_publication(destination, record, policy=policy)
            assert observation.inventory_valid == (not database_only)
            assert not observation.identity_valid
        if not database_only:
            assert (
                capture(destination / "skills" / NAME).entries[0].inode
                != record.draft.staged_tree.entries[0].inode
            )
            previous = destination / ".skill-publications" / operation_id / "previous"
            assert previous.stat().st_mode & 0o777 == 0o700
            assert (previous / "SKILL.md").read_bytes() == b"previous version"
            assert (
                previous / "__pycache__/required.bin"
            ).read_bytes() == b"previous required asset"
            assert (previous / "__pycache__/required.bin").stat().st_mode & 0o777 == 0o600
            assert (
                destination / "skills" / NAME / ".cache/required.bin"
            ).read_bytes() == b"new required asset"
        transplanted = replace(case, home=destination)
        with opened(transplanted) as (_session, service):
            try:
                service.recover(operation_id)
            except SkillMutationError:
                pass
            else:
                raise AssertionError("Transplante ganhou autoridade")
        assert canonical(destination) == before
        assert case.source.exists()
    # Mesmo endereço lógico, mas inodes novos: continua sem autoridade para remover.
    case.home.rename(case.home.with_name("preserved-home"))
    restore(archive, case.home)
    with observe_org_policy(OrgPolicyContext("file", case.policy_file), case.home) as policy:
        observation = validate_restored_publication(case.home, record, policy=policy)
        assert observation.inventory_valid and not observation.identity_valid
        assert not identify_skill(case.home, NAME, policy=policy).removivel
    with opened(case) as (session, service):
        assert not service.recover(operation_id).complete
        assert session.publications.get(operation_id).pending
    assert canonical(case.home) == before
    assert case.source.exists()
    return {
        "backup": "complete",
        "restore": "full-and-database-only",
        "authority_transferred": False,
    }


def main():
    assert os.getuid() == 10000
    assert Path(kairos_skills.__file__).is_relative_to("/opt/kairos")
    root, quarantine = Path("/acceptance-home"), Path("/acceptance-quarantine")
    assert root.stat().st_dev != quarantine.stat().st_dev, "Dois filesystems reais são obrigatórios"
    result = {
        "home_device": root.stat().st_dev,
        "quarantine_device": quarantine.stat().st_dev,
        "module": kairos_skills.__file__,
        "uid": os.getuid(),
    }
    phases = ("after_commit",) if sys.argv[1:] == ["cross-device"] else None
    result.update(run_matrix(root, quarantine, **({"phases": phases} if phases else {})))
    if phases is None:
        result.update(run_divergences(root, quarantine))
        result.update(run_backup_acceptance(root, quarantine))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
