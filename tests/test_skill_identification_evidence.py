"""Evidências de instalação conservam história sem conceder autoridade nova."""

import dataclasses
import os
import shutil
import sqlite3
import tempfile
import unittest
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

import kairos_filesystem.tree as tree_io
import kairos_skills.removal_origin as origin
from kairos_domain.ownership import Actor, Provenance
from kairos_filesystem.contract import TreeCapture
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import (
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    SkillMutationState,
)
from kairos_skills.mutation_io import (
    SkillMutationFiles,
    check_chain,
    open_directory,
    read_skill_creation,
)
from kairos_skills.mutations import SkillMutationService
from kairos_skills.removal_contract import SkillTreeAction, SkillTreeDraft
from kairos_skills.removal_io import SkillRemovalFiles, capture_skill
from kairos_state import connect
from kairos_state.migrations import migrate
from kairos_state.repositories.skill_mutations import SkillMutationRepository


class InstallationEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.name = "revisar-docs"
        self.source = self.root / "SKILL.md"
        self.source.write_text(creation_text())
        self.db = connect(self.home / "state.db")
        self.addCleanup(self.db.close)
        migrate(self.db)
        self.repo = SkillMutationRepository(self.db, home_id=catalog_home_id(self.home))
        self.service = SkillMutationService(self.home, self.repo)

    def create(self):
        return self.service.add(self.source, actor=Actor.USER_FOREGROUND)

    def remove(self):
        return self.service.remove(self.name, actor=Actor.USER_FOREGROUND, confirmed=True)

    def restore(self, removed):
        return self.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)

    def assert_evidence(self, *, current=None, creation=None, removed=None, bundled=False):
        evidence = self.repo.installation_evidence(self.name)
        projected = self.repo._projection(self.name)
        self.assertEqual(evidence.current, projected[0])
        self.assertEqual(projected[2], bundled)
        if current is None:
            self.assertIsNone(evidence.current)
        else:
            self.assertEqual(evidence.current.operation_id, current.operation_id)
            self.assertIs(evidence.current.provenance, current.provenance)
        self.assertEqual(
            evidence.historical_creation_id, None if creation is None else creation.operation_id
        )
        removed_id = None if removed is None else removed.operation_id
        self.assertEqual(evidence.current_removal_id, removed_id)
        self.assertEqual(None if projected[1] is None else projected[1].operation_id, removed_id)
        self.assertEqual(evidence.pending_operation_ids, ())

    def test_projection_and_evidence_share_transition_results(self):
        self.assert_evidence()
        created = self.create()
        self.assert_evidence(current=created, creation=created)
        removed = self.remove()
        self.assert_evidence(creation=created, removed=removed)
        restored = self.restore(removed)
        self.assert_evidence(current=restored, creation=created)
        newer_removal = self.remove()
        recreated = self.create()
        self.assert_evidence(current=recreated, creation=recreated, removed=newer_removal)
        self.service.rollback(recreated.operation_id, actor=Actor.USER_FOREGROUND)
        self.assert_evidence(removed=newer_removal)
        latest = self.create()
        self.assert_evidence(current=latest, creation=latest, removed=newer_removal)

    def test_restore_preserves_creation_without_history_truncation(self):
        created = self.create()
        for _ in range(51):
            removed = self.remove()
            self.assert_evidence(creation=created, removed=removed)
            restored = self.restore(removed)
            self.assert_evidence(current=restored, creation=created)
        evidence = self.repo.installation_evidence(self.name)
        self.assertEqual(evidence.current.operation_id, restored.operation_id)
        self.assertEqual(evidence.historical_creation_id, created.operation_id)
        self.assertEqual(evidence.pending_operation_ids, ())
        self.assertNotIn(created, self.repo.history(name=self.name, limit=100))

    def test_bundled_remove_restore_does_not_invent_creation(self):
        target = self.home / "skills" / self.name
        target.mkdir(parents=True)
        (target / "SKILL.md").write_bytes(b"# Conteudo legado sem semver\n")
        bundle = self.root / "bundle"
        shutil.copytree(target, bundle / self.name)
        (target.parent / ".bundled_manifest").write_text(self.name + ":" + "0" * 32 + "\n")
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            removed = self.remove()
        self.assert_evidence(removed=removed, bundled=True)
        restored = self.restore(removed)
        self.assert_evidence(current=restored)
        self.assertIs(restored.provenance, Provenance.BUNDLED)

    def test_independent_bundle_restore_does_not_inherit_previous_user_creation(self):
        created = self.create()
        first_removal = self.remove()
        self.assert_evidence(creation=created, removed=first_removal)

        bundle = self.root / "bundle"
        bundled = bundle / self.name
        bundled.mkdir(parents=True)
        contents = b"# Conteudo BUNDLED independente\n"
        (bundled / "SKILL.md").write_bytes(contents)
        target = self.home / "skills" / self.name
        shutil.copytree(bundled, target)
        (target.parent / ".bundled_manifest").write_text(self.name + ":" + "0" * 32 + "\n")
        with patch("kairos_skills.removal_origin.trusted_bundle_root", return_value=bundle):
            removed = self.remove()
        self.assertIs(removed.provenance, Provenance.BUNDLED)
        self.assert_evidence(removed=removed, bundled=True)
        self.assertEqual(self.repo._projection(self.name), (None, removed, True))
        self.assertIn(self.name, self.repo.bundled_tombstones())

        restored = self.restore(removed)
        self.assert_evidence(current=restored)
        self.assertIs(restored.provenance, Provenance.BUNDLED)
        self.assertEqual(self.repo._projection(self.name)[1:], (None, False))
        self.assertNotIn(self.name, self.repo.bundled_tombstones())
        self.assertEqual((target / "SKILL.md").read_bytes(), contents)
        history = self.repo.history(name=self.name)
        for record in (created, first_removal, removed, restored):
            self.assertIs(record.state, SkillMutationState.COMMITTED)
            self.assertIn(record, history)
            self.assertEqual(self.repo.get(record.operation_id), record)

    def test_pending_create_does_not_activate_identity(self):
        creation = read_skill_creation(self.source)
        operation_id = uuid.uuid4().hex
        with SkillMutationFiles(self.home) as files:
            entry = files.stage(operation_id, creation)
            draft = SkillMutationDraft(
                operation_id,
                self.repo.home_id,
                SkillMutationAction.CREATE,
                self.name,
                Actor.USER_FOREGROUND,
                1.0,
                entry.identity,
                creation.sha256,
                creation.size_bytes,
            )
            self.repo.prepare(draft, creation.text)
        for state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
            if state is SkillMutationState.CONFLICT:
                self.repo.finish(operation_id, state)
            with self.subTest(state=state):
                evidence = self.repo.installation_evidence(self.name)
                self.assertIsNone(evidence.current)
                self.assertIsNone(evidence.historical_creation_id)
                self.assertIsNone(evidence.current_removal_id)
                self.assertEqual(evidence.pending_operation_ids, (operation_id,))
        self.repo.finish(operation_id, SkillMutationState.ABORTED)
        self.assert_evidence()

    def test_pending_remove_keeps_committed_identity(self):
        created = self.create()
        with SkillRemovalFiles(self.home) as files:
            capture = files.capture_installed(self.name)
        operation_id = uuid.uuid4().hex
        draft = SkillTreeDraft(
            operation_id,
            self.repo.home_id,
            SkillTreeAction.REMOVE,
            self.name,
            Actor.USER_FOREGROUND,
            1.0,
            Provenance.USER,
            TreeCapture(capture.entries, None),
            operation_id,
        )
        self.repo.prepare_remove(draft, capture)
        for state in (SkillMutationState.PREPARED, SkillMutationState.CONFLICT):
            if state is SkillMutationState.CONFLICT:
                self.repo.finish(operation_id, state)
            with self.subTest(state=state):
                evidence = self.repo.installation_evidence(self.name)
                self.assertEqual(evidence.current.operation_id, created.operation_id)
                self.assertEqual(evidence.historical_creation_id, created.operation_id)
                self.assertIsNone(evidence.current_removal_id)
                self.assertEqual(evidence.pending_operation_ids, (operation_id,))
        self.repo.finish(operation_id, SkillMutationState.ABORTED)
        self.assert_evidence(current=created, creation=created)

    def test_corrupt_snapshot_raises_instead_of_returning_evidence(self):
        self.create()
        removed = self.remove()
        self.db.execute("DROP TRIGGER skill_tree_snapshots_no_update")
        with self.db:
            self.db.execute(
                "UPDATE skill_tree_snapshots SET manifest_json='[]' WHERE snapshot_id=?",
                (removed.operation_id,),
            )
        with self.assertRaises(SkillMutationError) as raised:
            self.repo.installation_evidence(self.name)
        self.assertEqual(raised.exception.kind, "corrupt")

    def test_corrupt_creation_record_raises_instead_of_returning_evidence(self):
        self.create()
        self.db.execute("DROP TRIGGER skill_mutation_operations_no_update")
        with self.db:
            self.db.execute("UPDATE skill_mutation_operations SET identity_json='{}'")
        with self.assertRaises(SkillMutationError) as raised:
            self.repo.installation_evidence(self.name)
        self.assertEqual(raised.exception.kind, "corrupt")

    def test_evidence_is_immutable_and_queries_do_not_register_writer(self):
        created = self.create()
        self.db.create_function("kairos_skill_writer_version", 0, None)
        changes = self.db.total_changes
        before = {path.relative_to(self.home) for path in self.home.rglob("*")}

        def readonly(action, *_):
            return (
                sqlite3.SQLITE_OK
                if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION)
                else sqlite3.SQLITE_DENY
            )

        self.db.set_authorizer(readonly)
        self.addCleanup(self.db.set_authorizer, None)
        evidence = self.repo.installation_evidence(self.name)
        self.assertEqual(evidence.current.operation_id, created.operation_id)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            evidence.current_removal_id = uuid.uuid4().hex
        with self.assertRaises(sqlite3.OperationalError):
            self.db.execute("SELECT kairos_skill_writer_version()")
        self.assertEqual(self.db.total_changes, changes)
        self.assertEqual({path.relative_to(self.home) for path in self.home.rglob("*")}, before)


class OriginEvidenceTests(unittest.TestCase):
    @contextmanager
    def fixture(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home = root / "home"
            target = home / "skills" / "example"
            target.mkdir(parents=True, mode=0o700)
            (target / "SKILL.md").write_bytes(b"# Legado sem semver\n")
            (target / "SKILL.md").chmod(0o600)
            (target / "scripts").mkdir(mode=0o700)
            (target / "scripts/run.bin").write_bytes(b"\x00\xff")
            (target / "scripts/run.bin").chmod(0o750)
            (target / ".empty").mkdir(mode=0o710)
            bundle = root / "package-skills"
            shutil.copytree(target, bundle / "example")
            source = root / "SKILL.md"
            source.write_text(creation_text(name="example"))
            db = connect(home / "state.db")
            try:
                migrate(db)
                repo = SkillMutationRepository(db, home_id=catalog_home_id(home))
                fixture = SimpleNamespace(
                    root=root,
                    home=home,
                    target=target,
                    bundle=bundle,
                    manifest=target.parent / ".bundled_manifest",
                    source=source,
                    db=db,
                    repo=repo,
                    service=SkillMutationService(home, repo),
                    current=None,
                )
                yield fixture
            finally:
                bundle.chmod(0o700)
                if (bundle / "example").is_dir():
                    (bundle / "example").chmod(0o700)
                db.close()

    def capture(self, fixture):
        with ExitStack() as stack:
            installed, chain = open_directory(stack, fixture.target.parent)
            captured = capture_skill(installed, "example")
            check_chain(chain)
            return captured

    def install_user(self, fixture):
        fixture.target.rename(fixture.root / "original")
        fixture.service.add(fixture.source, actor=Actor.USER_FOREGROUND)
        (fixture.target / "SKILL.md").write_bytes(b"Editado sem semver\n")
        fixture.current = fixture.repo.current_installation("example")

    def normal_case(self, fixture, case):
        fixture.manifest.write_text("example:" + "0" * 32 + "\n")
        if case in ("current_user", "replaced_root"):
            fixture.manifest.unlink()
            self.install_user(fixture)
            fixture.manifest.write_bytes(b"\xffmanifesto invalido ignorado com current")
            fixture.bundle = fixture.root / "bundle-unavailable"
            if case == "replaced_root":
                fixture.target.rename(fixture.root / "proven")
                shutil.copytree(fixture.root / "proven", fixture.target)
        elif case == "current_bundled":
            removed = fixture.service.remove("example", actor=Actor.USER_FOREGROUND, confirmed=True)
            fixture.service.rollback(removed.operation_id, actor=Actor.USER_FOREGROUND)
            fixture.current = fixture.repo.current_installation("example")
            fixture.manifest.write_bytes(b"\xffmanifesto invalido ignorado com current")
            fixture.bundle = fixture.root / "bundle-unavailable"
        elif case == "unknown_origin":
            fixture.manifest.unlink()
        elif case == "legacy_manifest":
            fixture.manifest.write_text("# Manifesto antigo\n\nexample\nother\n")
        elif case == "not_in_manifest":
            fixture.manifest.write_text("other:" + "a" * 32 + "\n")
        elif case == "empty_manifest":
            fixture.manifest.write_text("# Nenhuma entrada\n\n")
        elif case == "not_in_package_bundle":
            (fixture.bundle / "example").rename(fixture.bundle / "other")
        elif case == "bundle_bytes_mismatch":
            (fixture.bundle / "example/scripts/run.bin").write_bytes(b"\xff\x00")
        elif case == "bundle_inventory_mismatch":
            (fixture.bundle / "example/extra").mkdir()
        elif case == "bundle_modes_mismatch":
            (fixture.bundle / "example/scripts/run.bin").chmod(0o700)
        elif case != "verified_bundle":
            self.fail("Caso normal desconhecido: " + case)

    def tree_state(self, root):
        state = {}
        for path in (root, *sorted(root.rglob("*"))):
            value = path.lstat()
            state[path.relative_to(root)] = (
                value.st_dev,
                value.st_ino,
                value.st_mode,
                value.st_nlink,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
                path.read_bytes() if path.is_file() else None,
            )
        return state

    def test_resolve_and_prove_agree_for_every_normal_outcome(self):
        cases = (
            ("current_user", Provenance.USER, True, "proven_write", ()),
            ("current_bundled", Provenance.BUNDLED, True, "proven_write", ()),
            ("replaced_root", None, False, "none", ("replaced_root",)),
            ("unknown_origin", None, None, "none", ("unknown_origin",)),
            ("legacy_manifest", None, None, "none", ("legacy_manifest",)),
            ("not_in_manifest", None, None, "none", ("not_in_manifest",)),
            ("empty_manifest", None, None, "none", ("not_in_manifest",)),
            ("not_in_package_bundle", None, None, "none", ("not_in_package_bundle",)),
            ("bundle_bytes_mismatch", None, None, "none", ("bundle_mismatch",)),
            ("bundle_inventory_mismatch", None, None, "none", ("bundle_mismatch",)),
            ("bundle_modes_mismatch", None, None, "none", ("bundle_mismatch",)),
            ("verified_bundle", Provenance.BUNDLED, None, "verified_bundle", ()),
        )
        for case, provenance, identity, basis, reasons in cases:
            with (
                self.subTest(case=case),
                self.fixture() as fixture,
                patch(
                    "kairos_skills.removal_origin.trusted_bundle_root",
                    side_effect=lambda: fixture.bundle,
                ),
            ):
                self.normal_case(fixture, case)
                captured = self.capture(fixture)
                before = self.tree_state(fixture.home)
                evidence = origin.resolve_removal_origin(
                    fixture.home, "example", fixture.current, captured
                )
                self.assertIs(evidence.provenance, provenance)
                self.assertIs(evidence.identity_valid, identity)
                self.assertEqual(evidence.authorization_basis, basis)
                self.assertEqual(evidence.reasons, reasons)
                if provenance is None:
                    with self.assertRaises(SkillMutationError) as raised:
                        origin.prove_removal_origin(
                            fixture.home, "example", fixture.current, captured
                        )
                    self.assertEqual(raised.exception.kind, "conflict")
                else:
                    self.assertIs(
                        origin.prove_removal_origin(
                            fixture.home, "example", fixture.current, captured
                        ),
                        provenance,
                    )
                self.assertEqual(self.tree_state(fixture.home), before)
                with self.assertRaises(dataclasses.FrozenInstanceError):
                    evidence.authorization_basis = "proven_write"

    def error_case(self, fixture, case, captured, stack):
        fixture.manifest.write_text("example:" + "0" * 32 + "\n")
        invalid_manifests = {
            "invalid_utf8": b"\xff\n",
            "invalid_name": ("../escape:" + "0" * 32 + "\n").encode(),
            "invalid_hash": b"example:not-a-hash\n",
            "duplicate_v1": b"example\nexample\n",
            "duplicate_v2": ("example:" + "0" * 32 + "\n").encode() * 2,
            "mixed_versions": ("example\nother:" + "a" * 32 + "\n").encode(),
        }
        if case in invalid_manifests:
            fixture.manifest.write_bytes(invalid_manifests[case])
        elif case in (
            "manifest_symlink",
            "manifest_hardlink",
            "manifest_directory",
            "manifest_oversized",
        ):
            self.manifest_error(fixture, case)
        elif case in (
            "bundle_absent",
            "bundle_unreadable",
            "bundle_invalid_name",
            "bundle_duplicate",
            "bundle_symlink",
            "bundle_skill_hardlink",
            "bundle_asset_symlink",
            "bundle_asset_hardlink",
        ):
            self.bundle_error(fixture, case)
        elif case == "installed_symlink":
            (fixture.target / "link").symlink_to(fixture.source)
        elif case == "installed_hardlink":
            os.link(fixture.target / "scripts/run.bin", fixture.root / "linked-asset")
        elif case == "installed_limit":
            (fixture.target / "SKILL.md").write_bytes(b"x" * 65537)
        elif case == "attestation_unavailable":
            stack.enter_context(patch.object(tree_io.os, "listxattr", side_effect=OSError))
        elif case == "manifest_changed":
            original = origin.read_regular

            def read_then_change(parent, name, limit):
                result = original(parent, name, limit)
                fixture.manifest.write_text("other:" + "a" * 32 + "\n")
                return result

            stack.enter_context(patch.object(origin, "read_regular", read_then_change))
        elif case in ("installed_changed", "installed_chain_changed", "bundle_changed"):
            self.capture_race(fixture, case, captured, stack)
        elif case == "bundle_inventory_changed":
            self.bundle_inventory_race(fixture, stack)
        else:
            self.fail("Caso de erro desconhecido: " + case)

    def manifest_error(self, fixture, case):
        if case == "manifest_symlink":
            fixture.manifest.rename(fixture.root / "manifest")
            fixture.manifest.symlink_to(fixture.root / "manifest")
        elif case == "manifest_hardlink":
            os.link(fixture.manifest, fixture.root / "manifest")
        elif case == "manifest_directory":
            fixture.manifest.unlink()
            fixture.manifest.mkdir()
        elif case == "manifest_oversized":
            fixture.manifest.write_bytes(b"#" * (1024 * 1024 + 1))

    def bundle_error(self, fixture, case):
        if case == "bundle_absent":
            fixture.bundle = fixture.root / "absent"
        elif case == "bundle_unreadable":
            fixture.bundle.chmod(0)
        elif case == "bundle_invalid_name":
            (fixture.bundle / "example").rename(fixture.bundle / "_invalid")
        elif case == "bundle_duplicate":
            shutil.copytree(fixture.target, fixture.bundle / "category/example")
        elif case == "bundle_symlink":
            (fixture.bundle / "other").symlink_to(fixture.target, target_is_directory=True)
        elif case == "bundle_skill_hardlink":
            os.link(fixture.bundle / "example/SKILL.md", fixture.root / "linked-skill")
        elif case == "bundle_asset_symlink":
            (fixture.bundle / "example/link").symlink_to(fixture.source)
        elif case == "bundle_asset_hardlink":
            os.link(fixture.bundle / "example/scripts/run.bin", fixture.root / "linked-asset")

    def capture_race(self, fixture, case, captured, stack):
        original = origin.capture_skill
        changed = False

        def capture_then_change(parent, name):
            nonlocal changed
            result = original(parent, name)
            is_installed = result.entries[0].inode == captured.entries[0].inode
            if not changed and (is_installed != (case == "bundle_changed")):
                changed = True
                if case == "installed_chain_changed":
                    fixture.target.parent.rename(fixture.root / "old-skills")
                    shutil.copytree(fixture.root / "old-skills", fixture.target.parent)
                else:
                    target = fixture.target if is_installed else fixture.bundle / "example"
                    (target / "scripts/run.bin").write_bytes(b"editado durante captura")
            return result

        stack.enter_context(patch.object(origin, "capture_skill", capture_then_change))

    def bundle_inventory_race(self, fixture, stack):
        (fixture.bundle / "example").rename(fixture.root / "removed-bundle-entry")
        original = origin.check_chain
        changed = False

        def check_then_publish(chain):
            nonlocal changed
            original(chain)
            if not changed and any(
                os.fstat(fd).st_ino == fixture.bundle.stat().st_ino for _, _, fd in chain
            ):
                changed = True
                shutil.copytree(fixture.target, fixture.bundle / "example")

        stack.enter_context(patch.object(origin, "check_chain", check_then_publish))

    def test_resolve_and_prove_preserve_attestation_errors(self):
        cases = (
            ("invalid_utf8", "conflict"),
            ("invalid_name", "conflict"),
            ("invalid_hash", "conflict"),
            ("duplicate_v1", "conflict"),
            ("duplicate_v2", "conflict"),
            ("mixed_versions", "conflict"),
            ("manifest_symlink", "conflict"),
            ("manifest_hardlink", "conflict"),
            ("manifest_directory", "conflict"),
            ("manifest_oversized", "conflict"),
            ("bundle_absent", "io"),
            ("bundle_unreadable", "io"),
            ("bundle_invalid_name", "conflict"),
            ("bundle_duplicate", "conflict"),
            ("bundle_symlink", "conflict"),
            ("bundle_skill_hardlink", "conflict"),
            ("bundle_asset_symlink", "conflict"),
            ("bundle_asset_hardlink", "conflict"),
            ("installed_symlink", "conflict"),
            ("installed_hardlink", "conflict"),
            ("installed_limit", "conflict"),
            ("attestation_unavailable", "unavailable"),
            ("manifest_changed", "conflict"),
            ("installed_changed", "conflict"),
            ("installed_chain_changed", "conflict"),
            ("bundle_changed", "conflict"),
            ("bundle_inventory_changed", "conflict"),
        )
        for case, kind in cases:
            for method in ("resolve_removal_origin", "prove_removal_origin"):
                with self.subTest(case=case, method=method), self.fixture() as fixture:
                    captured = self.capture(fixture)
                    with ExitStack() as stack:
                        stack.enter_context(
                            patch(
                                "kairos_skills.removal_origin.trusted_bundle_root",
                                side_effect=lambda: fixture.bundle,
                            )
                        )
                        self.error_case(fixture, case, captured, stack)
                        with self.assertRaises(SkillMutationError) as raised:
                            getattr(origin, method)(fixture.home, "example", None, captured)
                        self.assertEqual(raised.exception.kind, kind)

    def test_name_refusal_precedes_filesystem_access(self):
        for method in ("resolve_removal_origin", "prove_removal_origin"):
            for name in ("_org", "../escape", "not valid"):
                with self.subTest(method=method, name=name):
                    with self.assertRaises(SkillMutationError) as raised:
                        getattr(origin, method)(Path("/does-not-exist"), name, None, None)
                    self.assertEqual(raised.exception.kind, "input")
