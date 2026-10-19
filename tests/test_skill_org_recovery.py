"""Recuperação real conserva recibos e recusa autorização retroativa."""

import multiprocessing
from unittest.mock import patch

from skill_org_fixtures import bound_policy, personal_policy
from test_skill_mutation_state import creation_draft
from test_skill_org_services import OrgServiceFixture

from kairos_domain.ownership import Actor
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationError, SkillMutationState
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutation_lock import skill_mutation_lock
from kairos_skills.mutation_recovery import reconcile_create, reconcile_pending
from kairos_skills.mutations import SkillMutationService
from kairos_skills.org_policy import observe_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository
from kairos_state.repositories.skill_org_protections import SkillOrgProtectionRepository


def stopped_org_creation(home, source, policy_source, phase, pipe):
    with observe_org_policy(OrgPolicyContext("file", policy_source), home) as policy:
        connection = connect(home / "state.db")
        try:
            migrate(connection)
            repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
            prepare, publish, finish = (
                repository.prepare,
                SkillMutationFiles.publish,
                repository.finish,
            )

            def stop(operation_id):
                pipe.send(operation_id)
                pipe.recv()

            def prepared(draft, text, **kwargs):
                result = prepare(draft, text, **kwargs)
                if phase == "prepared":
                    stop(draft.operation_id)
                return result

            def published(files, operation_id, name):
                publish(files, operation_id, name)
                if phase == "published":
                    stop(operation_id)

            def finishing(operation_id, state):
                if phase == "before-commit":
                    stop(operation_id)
                return finish(operation_id, state)

            repository.prepare, repository.finish = prepared, finishing
            with patch.object(SkillMutationFiles, "publish", published):
                SkillMutationService(home, repository, policy=policy).add(
                    source, actor=Actor.USER_FOREGROUND
                )
        finally:
            connection.close()


class OrgRecoveryTests(OrgServiceFixture):
    def interrupt(self, policy):
        with (
            patch.object(
                self.repository, "finish", side_effect=SkillMutationError("io", "interrupted")
            ),
            self.assertRaises(SkillMutationError),
        ):
            self.service(policy).add(self.source, actor=Actor.USER_FOREGROUND)
        return self.repository.pending("org-skill")[0]

    def reconcile(self, policy):
        with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
            return reconcile_pending(self.repository, files, name="org-skill", policy=policy)

    def test_prepared_binding_survives_current_policy_removal(self):
        record = self.interrupt(self.policy)
        recovered = self.reconcile(self.none)
        self.assertEqual(recovered[0].operation_id, record.operation_id)
        self.assertEqual(recovered[0].state, SkillMutationState.COMMITTED)
        self.assertTrue(self.protections.protected_organizations("org-skill"))

    def test_new_binding_blocks_completion_of_personal_prepared_installation(self):
        record = self.interrupt(self.none)
        before = (self.home / "skills/org-skill/SKILL.md").read_bytes()
        with self.assertRaises(SkillMutationError) as caught:
            self.reconcile(self.policy)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual((self.home / "skills/org-skill/SKILL.md").read_bytes(), before)
        self.assertEqual(
            self.repository.get(record.operation_id).state, SkillMutationState.CONFLICT
        )
        self.assertFalse(self.protections.protected_organizations("org-skill"))

    def test_v9_pending_without_receipt_is_not_authorized_by_new_binding(self):
        home = self.root / "legacy-home"
        home.mkdir()
        connection = connect(home / "state.db")
        self.addCleanup(connection.close)
        migrate(connection, target=9)
        repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
        creation = read_skill_creation(self.source)
        draft = creation_draft(home, creation)
        from dataclasses import replace

        with SkillMutationFiles(home) as files:
            entry = files.stage(draft.operation_id, creation)
            draft = replace(draft, identity=entry.identity)
            repository.prepare(draft, creation.text)
            files.publish(draft.operation_id, creation.name)
        migrate(connection, target=10)
        policy = bound_policy(self, home, self.root / "new-binding.json")
        with (
            skill_mutation_lock(home),
            SkillMutationFiles(home) as files,
            self.assertRaises(SkillMutationError) as caught,
        ):
            reconcile_pending(repository, files, name=creation.name, policy=policy)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertTrue((home / "skills/org-skill/SKILL.md").is_file())
        self.assertIsNone(
            SkillOrgProtectionRepository(connection, home_id=repository.home_id).prepared_receipt(
                draft.operation_id
            )
        )

    def test_new_veto_after_retirement_preserves_pending_and_retired_tree(self):
        self.service().add(self.source, actor=Actor.USER_FOREGROUND)
        with (
            patch.object(
                self.repository, "finish", side_effect=SkillMutationError("io", "interrupted")
            ),
            self.assertRaises(SkillMutationError),
        ):
            self.service().remove("org-skill", actor=Actor.USER_FOREGROUND, confirmed=True)
        record = self.repository.pending("org-skill")[0]
        retired = self.home / ".skill-mutations/retired" / record.operation_id / "SKILL.md"
        before = retired.read_bytes()
        with self.assertRaises(SkillMutationError) as caught:
            self.reconcile(self.policy)
        self.assertEqual(caught.exception.kind, "denied")
        self.assertEqual(retired.read_bytes(), before)
        self.assertFalse((self.home / "skills/org-skill").exists())
        self.assertEqual(
            self.repository.get(record.operation_id).state, SkillMutationState.PREPARED
        )

    def test_direct_recovery_requires_policy_and_refuses_changed_source(self):
        record = self.interrupt(self.policy)
        (self.root / "private-source-sentinel.json").unlink()
        with skill_mutation_lock(self.home), SkillMutationFiles(self.home) as files:
            with self.assertRaises(TypeError):
                reconcile_create(self.repository, files, record)
            with self.assertRaises(SkillMutationError) as caught:
                reconcile_create(self.repository, files, record, policy=self.policy)
        self.assertEqual(caught.exception.kind, "conflict")
        self.assertEqual(
            self.repository.get(record.operation_id).state, SkillMutationState.PREPARED
        )
        with (
            self.assertRaises(SkillMutationError) as caught,
            observe_org_policy(
                OrgPolicyContext("file", self.root / "private-source-sentinel.json"), self.home
            ),
        ):
            self.fail("missing source accepted")
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertTrue((self.home / "skills/org-skill/SKILL.md").is_file())

    def test_sigkill_preserves_prepared_binding_at_each_publication_boundary(self):
        context = multiprocessing.get_context("spawn")
        for phase in ("prepared", "published", "before-commit"):
            with self.subTest(phase=phase):
                home = self.root / phase
                home.mkdir()
                source = self.root / (phase + ".json")
                bound_policy(self, home, source)
                parent, child = context.Pipe()
                process = context.Process(
                    target=stopped_org_creation, args=(home, self.source, source, phase, child)
                )
                process.start()
                try:
                    self.assertTrue(parent.poll(15), "process did not reach real boundary")
                    operation_id = parent.recv()
                    process.kill()
                    process.join(5)
                    self.assertEqual(process.exitcode, -9)
                finally:
                    if process.is_alive():
                        process.kill()
                        process.join(5)
                    parent.close()
                    child.close()
                connection = connect(home / "state.db")
                try:
                    repository = SkillMutationRepository(connection, home_id=catalog_home_id(home))
                    self.assertEqual(
                        repository.get(operation_id).state, SkillMutationState.PREPARED
                    )
                    with skill_mutation_lock(home), SkillMutationFiles(home) as files:
                        reconcile_pending(
                            repository, files, name="org-skill", policy=personal_policy(self, home)
                        )
                    state = (
                        SkillMutationState.ABORTED
                        if phase == "prepared"
                        else SkillMutationState.COMMITTED
                    )
                    self.assertEqual(repository.get(operation_id).state, state)
                    protections = SkillOrgProtectionRepository(
                        connection, home_id=repository.home_id
                    )
                    self.assertEqual(
                        bool(protections.protected_organizations("org-skill")), phase != "prepared"
                    )
                finally:
                    connection.close()
