"""Regressões de disponibilidade e recusa observadas na revisão B2."""

import argparse
import asyncio
import io
import json
import os
import sqlite3
import subprocess
import tarfile
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from skill_publication_writer_fixtures import acquired_source
from test_skill_publication_backup import rows
from test_skill_publication_files import capture
from test_skill_publication_service import Interrupted
from test_skill_publication_writers import PublicationWriterFixture

from kairos_cli.backup import run_backup
from kairos_skills import publications, sync
from kairos_skills.hub import SkillBundle
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_skills.publication_contract import ManifestDelta
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_bundled_source
from kairos_skills.publications import SkillPublicationService


class PublicationSyncReviewTests(PublicationWriterFixture):
    def pending_quarantine_cleanup(self):
        quarantine = self.root / "quarantine"
        _source, context, acquisition_id = acquired_source(
            self,
            self.home,
            quarantine,
            SkillBundle("personal-skill", {"SKILL.md": b"quarantine version"}),
            policy=self.policy,
        )
        original = publications.delete_publication

        def interrupt(*args, **kwargs):
            original(*args, **kwargs)
            raise Interrupted

        with publication_session(self.home, policy=self.policy, context=context) as session:
            with (
                patch.object(publications, "delete_publication", new=interrupt),
                self.assertRaises(Interrupted),
            ):
                SkillPublicationService(session).publish(
                    "personal-skill", acquisition_id=acquisition_id
                )
            return session.publications.history(name="personal-skill")[-1]

    def test_quarantine_pending_blocks_only_its_name(self):
        retired = self.skill("old-catalog")
        self.assertFalse(self.sync().failed)
        retired.rename(self.root / "retired-package")
        pending = self.pending_quarantine_cleanup()
        target = self.home / "skills/personal-skill"
        installed = capture(target)
        self.skill()
        self.skill("unrelated")
        result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.pending, [pending.operation_id])
        self.assertEqual(result.copied, ["unrelated"])
        self.assertEqual(result.cleaned, ["old-catalog"])
        self.assertEqual(capture(target), installed)
        self.assertEqual((self.home / "skills/unrelated/SKILL.md").read_bytes(), b"package version")
        self.assertNotIn(
            "old-catalog", sync.read_manifest(self.home / "skills" / sync.MANIFEST_NAME)
        )
        with (
            self.context() as context,
            publication_session(self.home, policy=self.policy, context=context) as session,
        ):
            self.assertEqual(session.publications.get(pending.operation_id), pending)

    def test_bundled_pending_does_not_block_independent_publication(self):
        source = self.skill("blocked-skill")
        with (
            self.context() as context,
            publication_session(self.home, policy=self.policy, context=context) as session,
            patch.object(sync, "reconcile_manifest", side_effect=OSError("manifest unavailable")),
        ):
            pending = SkillPublicationService(session).publish(
                source.name,
                manifest_delta=ManifestDelta(source.name, None, sync.origin_hash(source)),
            )
        self.assertFalse(pending.complete)
        self.skill("unrelated")
        original = sync.reconcile_manifest

        def reconcile(home, record, **kwargs):
            if record.name == source.name:
                raise OSError("manifest unavailable")
            return original(home, record, **kwargs)

        with patch.object(sync, "reconcile_manifest", new=reconcile):
            result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.pending, [pending.operation_id])
        self.assertEqual(result.copied, ["unrelated"])
        self.assertTrue((self.home / "skills/unrelated/SKILL.md").is_file())

    def test_oversized_source_is_explicit_partial_failure_for_its_name(self):
        self.skill("healthy")
        self.skill("oversized", b"x" * 65537)
        result = self.sync()
        self.assertTrue(result.failed)
        self.assertEqual(result.copied, ["healthy"])
        self.assertEqual(result.error_kinds, ["input"])
        self.assertEqual(
            result.errors, [{"name": "oversized", "kind": "input", "operation_id": None}]
        )
        self.assertEqual((self.home / "skills/healthy/SKILL.md").read_bytes(), b"package version")
        self.assertFalse((self.home / "skills/oversized").exists())

    def test_cli_reports_partial_source_failure_as_json(self):
        from kairos_cli.main import main

        self.skill("healthy")
        self.skill("oversized", b"x" * 65537)
        out, error = io.StringIO(), io.StringIO()
        environment = os.environ | {"KAIROS_HOME": str(self.home)}
        environment.pop("KAIROS_SKILL_ORG_POLICY", None)
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "kairos_skills.publication_sources.trusted_bundle_root", return_value=self.bundle
            ),
            redirect_stdout(out),
            redirect_stderr(error),
        ):
            code = main(["sync", "now", "--json"])
        self.assertEqual(code, 2, error.getvalue())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["copiadas"], ["healthy"])
        self.assertTrue(payload["failed"])
        self.assertEqual(
            payload["erros"], [{"name": "oversized", "kind": "input", "operation_id": None}]
        )
        self.assertNotIn(str(self.root), out.getvalue() + error.getvalue())

    def archived_home(self):
        self.skill()
        operation_id = self.sync().publications[0].operation_id
        archive = self.root / "backup.tar.gz"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(
                asyncio.run(
                    run_backup(self.home, argparse.Namespace(output=str(archive), json=True))
                ),
                0,
            )
        return archive, operation_id

    def restore_home(self, archive, label, *, database_only):
        restored = self.root / label
        restored.mkdir(mode=0o700)
        with tarfile.open(archive) as tar:
            members = [tar.getmember("state.db")] if database_only else tar.getmembers()
            tar.extractall(restored, members=members, filter="data")
            for member in members:
                safe = tarfile.data_filter(member, str(restored))
                if safe is not None and (safe.isdir() or safe.isfile()):
                    (restored / safe.name).chmod(member.mode & 0o777)
        return restored

    def sync_home(self, home):
        with (
            observe_org_policy(OrgPolicyContext("none", None), home) as policy,
            patch(
                "kairos_skills.publication_sources.trusted_bundle_root", return_value=self.bundle
            ),
            observe_bundled_source(home, policy=policy) as context,
        ):
            return sync.sync_bundled_skills(
                self.bundle, home / "skills", policy=policy, context=context
            )

    def test_foreign_home_history_refuses_with_category_and_id(self):
        archive, operation_id = self.archived_home()
        for database_only in (False, True):
            with self.subTest(database_only=database_only):
                restored = self.restore_home(
                    archive, "api-" + str(database_only), database_only=database_only
                )
                with closing(sqlite3.connect(restored / "state.db")) as connection:
                    before = rows(connection)
                with (
                    self.assertLogs("kairos_skills.sync", level="WARNING") as logs,
                    self.assertRaises(SkillMutationError) as caught,
                ):
                    self.sync_home(restored)
                self.assertEqual(caught.exception.kind, "corrupt")
                self.assertEqual(caught.exception.operation_id, operation_id)
                self.assertEqual(logs.records[-1].operation_id, operation_id)
                self.assertNotIn(str(restored), str(caught.exception) + str(logs.output))
                with closing(sqlite3.connect(restored / "state.db")) as connection:
                    self.assertEqual(rows(connection), before)

    def test_cli_foreign_home_refuses_as_json_without_internal_exception(self):
        archive, operation_id = self.archived_home()
        for database_only in (False, True):
            with self.subTest(database_only=database_only):
                restored = self.restore_home(
                    archive, "cli-" + str(database_only), database_only=database_only
                )
                env = os.environ | {"KAIROS_HOME": str(restored)}
                env.pop("KAIROS_SKILL_ORG_POLICY", None)
                result = subprocess.run(
                    [str(Path(".venv/bin/kairos").absolute()), "sync", "now", "--json"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=False,
                )
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stdout.strip(), result.stderr)
                payload = json.loads(result.stdout)
                self.assertTrue(payload["failed"])
                self.assertEqual(payload["copiadas"], [])
                self.assertEqual(payload["erros"][0]["kind"], "corrupt")
                self.assertEqual(payload["erros"][0]["operation_id"], operation_id)
                self.assertNotIn("AttributeError", result.stderr)
                self.assertNotIn(str(restored), result.stdout + result.stderr)
