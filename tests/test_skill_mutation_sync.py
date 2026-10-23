"""Sync participa do lock e conserva autoria local sem origem bundled."""

import multiprocessing
import unittest
from unittest.mock import patch

import test_skill_mutation_service as service_fixtures
from skill_org_fixtures import personal_policy, personal_sync_operation
from skill_publication_writer_fixtures import sync_bundled_skills
from test_skill_mutation_io import hold_lock

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.sync import (
    MANIFEST_NAME,
    NO_BUNDLED_SKILLS_MARKER,
    read_manifest,
)


@personal_sync_operation
def sync_process(bundle, home, pipe, *, policy):
    from kairos_skills import sync
    from kairos_skills.publication_sources import observe_bundled_source

    original = sync._sync_discovered

    def mutate(session, result):
        pipe.send("mutation_started")
        return original(session, result)

    with (
        patch("kairos_skills.publication_sources.trusted_bundle_root", return_value=bundle),
        observe_bundled_source(home, policy=policy) as context,
    ):
        for name in context._bundled:
            context.resolve(name)
        pipe.send("snapshot_ready")
        with patch.object(sync, "_sync_discovered", side_effect=mutate):
            result = sync.sync_bundled_skills(
                bundle, home / "skills", policy=policy, context=context
            )
    pipe.send((result.copied, result.user_modified))


class MutationSyncTests(unittest.TestCase):
    def setUp(self):
        service_fixtures.MutationServiceTests.setUp(self)
        self.service.add(self.source, actor=Actor.USER_FOREGROUND)
        self.bundle = self.root / "bundle"
        for name in ("revisar-docs", "new-bundled"):
            source = self.bundle / name
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text("bundled fixture")
        self.context = multiprocessing.get_context("spawn")

    def test_sync_waits_for_mutation_and_preserves_local(self):
        lock_parent, lock_child = self.context.Pipe()
        sync_parent, sync_child = self.context.Pipe()
        holder = self.context.Process(target=hold_lock, args=(self.home, lock_child))
        syncing = self.context.Process(
            target=sync_process, args=(self.bundle, self.home, sync_child)
        )
        holder.start()
        try:
            self.assertTrue(lock_parent.poll(10))
            self.assertEqual(lock_parent.recv(), "locked")
            syncing.start()
            self.assertTrue(sync_parent.poll(10))
            self.assertEqual(sync_parent.recv(), "snapshot_ready")
            self.assertFalse(sync_parent.poll(0.15), "sync entered mutation while lock held")
            self.assertFalse((self.home / "skills" / "new-bundled").exists())
            lock_parent.send("release")
            self.assertTrue(sync_parent.poll(10))
            self.assertEqual(sync_parent.recv(), "mutation_started")
            self.assertTrue(sync_parent.poll(10))
            copied, preserved = sync_parent.recv()
            self.assertEqual(copied, ["new-bundled"])
            self.assertEqual(preserved, ["revisar-docs"])
            holder.join(5)
            syncing.join(5)
            self.assertEqual(holder.exitcode, 0)
            self.assertEqual(syncing.exitcode, 0)
            self.assertEqual(
                (self.home / "skills" / "revisar-docs" / "SKILL.md").read_bytes(),
                self.source.read_bytes(),
            )
            self.assertNotIn("revisar-docs", read_manifest(self.home / "skills" / MANIFEST_NAME))
        finally:
            for process in (holder, syncing):
                if process.is_alive():
                    process.kill()
                if process.pid is not None:
                    process.join(5)
            for pipe in (lock_parent, lock_child, sync_parent, sync_child):
                pipe.close()

    def test_sync_timeout_private_error_and_opt_out(self):
        parent, child = self.context.Pipe()
        holder = self.context.Process(target=hold_lock, args=(self.home, child))
        holder.start()
        try:
            self.assertTrue(parent.poll(10))
            self.assertEqual(parent.recv(), "locked")
            with (
                patch(
                    "kairos_skills.publication_session.skill_mutation_lock",
                    lambda home: skill_mutation_lock(home, timeout=0.05),
                ),
                self.assertRaises(SkillMutationError) as raised,
            ):
                sync_bundled_skills(
                    self.bundle,
                    self.home / "skills",
                    policy=personal_policy(self, ((self.home / "skills").parent)),
                )
            self.assertEqual(raised.exception.kind, "conflict")
            self.assertNotIn(str(self.home), str(raised.exception))
            self.assertFalse((self.home / "skills" / "new-bundled").exists())
            parent.send("release")
            holder.join(5)
        finally:
            if holder.is_alive():
                holder.kill()
                holder.join()
            parent.close()
            child.close()
        lock = self.home / ".skills-write.lock"
        lock.unlink()
        lock.symlink_to(self.root / "outside")
        with self.assertRaises(SkillMutationError):
            sync_bundled_skills(
                self.bundle,
                self.home / "skills",
                policy=personal_policy(self, ((self.home / "skills").parent)),
            )
        (self.home / "skills" / NO_BUNDLED_SKILLS_MARKER).touch()
        result = sync_bundled_skills(
            self.bundle,
            self.home / "skills",
            policy=personal_policy(self, ((self.home / "skills").parent)),
        )
        self.assertTrue(result.skipped_opt_out)
        self.assertFalse((self.root / "outside").exists())
