"""Publicação real, entrada estrita e contenção de skills manuais."""

import errno
import hashlib
import multiprocessing
import os
import shutil
import tempfile
import time
import unittest
import uuid
from enum import StrEnum
from pathlib import Path
from unittest.mock import patch

from skill_mutation_fixtures import creation_text

from kairos_domain.ownership import Actor
from kairos_skills.mutation_contract import (
    SkillCreation,
    SkillDirectoryIdentity,
    SkillMutationAction,
    SkillMutationDraft,
    SkillMutationError,
    validate_skill_creation,
)
from kairos_skills.mutation_io import SkillMutationFiles, read_skill_creation
from kairos_skills.mutation_lock import skill_mutation_lock


def hold_lock(home, pipe):
    with skill_mutation_lock(home):
        pipe.send("locked")
        pipe.recv()


def read_fifo(source, pipe):
    try:
        read_skill_creation(source)
    except SkillMutationError:
        pipe.send("refused")


class FilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.creation = validate_skill_creation(creation_text())
        self.operation = uuid.uuid4().hex

    def test_publish_nao_substitui(self):
        with SkillMutationFiles(self.home) as files:
            entry = files.stage(self.operation, self.creation)
            files.publish(self.operation, self.creation.name)
            self.assertEqual(files.inspect_installed(self.creation.name), entry)
            second = uuid.uuid4().hex
            files.stage(second, self.creation)
            with self.assertRaises(SkillMutationError):
                files.publish(second, self.creation.name)
            self.assertEqual(files.inspect_installed(self.creation.name), entry)
            files.retire(self.creation.name, self.operation)
            self.assertIsNone(files.inspect_installed(self.creation.name))
            self.assertEqual(files.inspect_private(self.operation, retired=True), entry)
            files.restore(self.operation, self.creation.name)
            self.assertEqual(files.inspect_installed(self.creation.name), entry)
        for kind in ("file", "directory", "link"):
            with self.subTest(kind=kind):
                target = self.home / "skills" / kind
                if kind == "file":
                    target.write_text("marker")
                elif kind == "directory":
                    target.mkdir()
                else:
                    target.symlink_to(self.home / "absent")
                with SkillMutationFiles(self.home) as files:
                    operation = uuid.uuid4().hex
                    files.stage(operation, validate_skill_creation(creation_text(name=kind)))
                    with self.assertRaises(SkillMutationError):
                        files.publish(operation, kind)
                    with self.assertRaises(SkillMutationError):
                        files.assert_name_available(kind)
                if kind == "file":
                    self.assertEqual(target.read_text(), "marker")
                elif kind == "directory":
                    self.assertEqual(list(target.iterdir()), [])
                else:
                    self.assertTrue(target.is_symlink())

    def test_private_desconhecido_preservado(self):
        with SkillMutationFiles(self.home) as files:
            unknown = self.home / ".skill-mutations" / "staging" / uuid.uuid4().hex
            unknown.mkdir()
            (unknown / "marker").write_text("preserve")
            own = files.stage(self.operation, self.creation)
            (self.home / ".skill-mutations" / "staging" / self.operation / "extra").write_text(
                "preserve"
            )
            with self.assertRaises(SkillMutationError):
                files.discard_staging(self.operation, own)
            self.assertEqual((unknown / "marker").read_text(), "preserve")
            self.assertTrue(
                (self.home / ".skill-mutations" / "staging" / self.operation / "extra").exists()
            )

    def test_copia_identica_nao_tem_identidade_original(self):
        with SkillMutationFiles(self.home) as files:
            original = files.stage(self.operation, self.creation)
            files.publish(self.operation, self.creation.name)
            target = self.home / "skills" / self.creation.name
            target.rename(self.home / "old")
            shutil.copytree(self.home / "old", target)
            copy = files.inspect_installed(self.creation.name)
            self.assertEqual(copy.creation, original.creation)
            self.assertNotEqual(copy.identity, original.identity)

    def test_platform_recusa_sem_fallback(self):
        with SkillMutationFiles(self.home) as files:
            original = files.stage(self.operation, self.creation)
            for number in (errno.ENOSYS, errno.EOPNOTSUPP, errno.EINVAL, errno.EXDEV):
                with (
                    self.subTest(errno=number),
                    patch(
                        "kairos_skills.mutation_io.ctypes.CDLL",
                        side_effect=OSError(number, "private"),
                    ),
                ):
                    with self.assertRaises(SkillMutationError) as raised:
                        files.publish(self.operation, self.creation.name)
                    self.assertEqual(raised.exception.kind, "unavailable")
                    self.assertEqual(files.inspect_private(self.operation), original)
                    self.assertIsNone(files.inspect_installed(self.creation.name))

    def test_manifest_tombstone_and_malformed_refused(self):
        with SkillMutationFiles(self.home) as files:
            manifest = self.home / "skills" / ".bundled_manifest"
            for contents in (
                "revisar-docs\n",
                "revisar-docs:" + "a" * 32 + "\n",
                "../escape:invalid\n",
                "other:invalid\n",
            ):
                manifest.write_text(contents)
                with self.subTest(contents=contents[:20]), self.assertRaises(SkillMutationError):
                    files.assert_name_available("revisar-docs")
            manifest.unlink()
            files.assert_name_available("revisar-docs")

    def test_links_home_skills_private_and_inspection_io_refused(self):
        for relative in (
            "skills",
            ".skill-mutations",
            ".skill-mutations/staging",
            ".skill-mutations/retired",
        ):
            with tempfile.TemporaryDirectory() as directory:
                home = Path(directory)
                entry = home / relative
                entry.parent.mkdir(exist_ok=True)
                entry.symlink_to(self.home, target_is_directory=True)
                with (
                    self.subTest(relative=relative),
                    self.assertRaises(SkillMutationError),
                    SkillMutationFiles(home),
                ):
                    pass
        with (
            SkillMutationFiles(self.home) as files,
            patch("kairos_skills.mutation_io.os.stat", side_effect=PermissionError("private")),
            self.assertRaises(SkillMutationError),
        ):
            files.inspect_installed("revisar-docs")

    def test_lock_link_timeout_e_release(self):
        ctx = multiprocessing.get_context("fork")
        parent, child = ctx.Pipe()
        process = ctx.Process(target=hold_lock, args=(self.home, child))
        process.start()
        try:
            self.assertTrue(parent.poll(2))
            self.assertEqual(parent.recv(), "locked")
            started = time.monotonic()
            with (
                self.assertRaises(SkillMutationError) as raised,
                skill_mutation_lock(self.home, timeout=0.05),
            ):
                self.fail("lock acquired")
            self.assertEqual(raised.exception.kind, "conflict")
            self.assertLess(time.monotonic() - started, 1)
            parent.send("release")
            process.join(2)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()
        with self.assertRaisesRegex(RuntimeError, "fixture"), skill_mutation_lock(self.home):
            raise RuntimeError("fixture")
        with skill_mutation_lock(self.home):
            pass
        with self.assertRaises(OSError), skill_mutation_lock(self.home):
            raise OSError("caller fixture")
        with skill_mutation_lock(self.home):
            pass
        lock = self.home / ".skills-write.lock"
        lock.unlink()
        lock.symlink_to(self.home / "outside")
        with self.assertRaises(SkillMutationError), skill_mutation_lock(self.home):
            pass
        self.assertFalse((self.home / "outside").exists())

    def test_fifo_refused_without_blocking(self):
        fifo = self.home / "fifo"
        os.mkfifo(fifo)
        parent, child = multiprocessing.get_context("fork").Pipe()
        process = multiprocessing.get_context("fork").Process(target=read_fifo, args=(fifo, child))
        process.start()
        try:
            self.assertTrue(parent.poll(2))
            self.assertEqual(parent.recv(), "refused")
            process.join(2)
            self.assertEqual(process.exitcode, 0)
        finally:
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def test_read_replacement(self):
        source = self.home / "SKILL.md"
        source.write_text(creation_text())
        real_read = os.read
        changed = False

        def read_and_replace(fd, count):
            nonlocal changed
            result = real_read(fd, count)
            if not changed:
                changed = True
                source.rename(self.home / "original")
                source.write_text(creation_text(body="outside fixture"))
            return result

        with (
            patch("kairos_skills.mutation_io.os.read", side_effect=read_and_replace),
            self.assertRaises(SkillMutationError),
        ):
            read_skill_creation(source)
        self.assertIn("outside fixture", source.read_text())


class CreationTests(unittest.TestCase):
    def test_creation_preserva_bytes(self):
        text = creation_text(body="Revisão fictícia.\r\n")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "SKILL.md"
            source.write_bytes(text.encode())
            creation = read_skill_creation(source)
            self.assertEqual(creation.text.encode(), source.read_bytes())
            self.assertEqual(creation.sha256, hashlib.sha256(text.encode()).hexdigest())
            self.assertEqual(creation.size_bytes, len(text.encode()))
            self.assertEqual(creation.name, "revisar-docs")

    def test_creation_rejeita_formatos(self):
        texts = [
            creation_text(description="x" * 61),
            creation_text(description="Sem ponto"),
            creation_text(version="antiga"),
            creation_text(body=" "),
            creation_text(name="../escape"),
            creation_text(name="n" * 65),
            creation_text().replace("name: revisar-docs", "name: [revisar-docs]"),
            creation_text().replace(
                "description: Revise documentos.", "description: &x ['a']\ntags: *x"
            ),
            creation_text(body="x" * 65536),
            creation_text(body="\ud800"),
        ]
        for text in texts:
            with self.subTest(text=text[:20]), self.assertRaises(SkillMutationError):
                validate_skill_creation(text)
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "SKILL.md"
            source.write_bytes(b"\xff")
            with self.assertRaises(SkillMutationError):
                read_skill_creation(source)

    def test_actor_e_identidade_nao_aceitam_coercao(self):
        for identity in [(True, 2, 3, 4), (1, 0, 3, 4), (1, 2, "3", 4)]:
            with self.subTest(identity=identity), self.assertRaises(SkillMutationError):
                SkillDirectoryIdentity(*identity)
        text = creation_text()
        creation = validate_skill_creation(text)
        for values in [
            (creation.name, text, "0" * 64, creation.size_bytes),
            (creation.name, text, creation.sha256, True),
            ("other", text, creation.sha256, creation.size_bytes),
        ]:
            with self.subTest(values=values[0]), self.assertRaises(SkillMutationError):
                SkillCreation(*values)
        for actor in [Actor.CURATOR, Actor.BACKGROUND_REVIEW, "user_foreground", 1]:
            with self.subTest(actor=actor), self.assertRaises(SkillMutationError):
                SkillMutationDraft(
                    uuid.uuid4().hex,
                    "0" * 64,
                    SkillMutationAction.CREATE,
                    "revisar-docs",
                    actor,
                    1.0,
                    SkillDirectoryIdentity(1, 2, 1, 4),
                    creation.sha256,
                    creation.size_bytes,
                )

    def test_creation_direct_fields_require_exact_types(self):
        class Name(StrEnum):
            SKILL = "revisar-docs"

        class Digest(str):
            pass

        text = creation_text()
        creation = validate_skill_creation(text)
        for name, digest in (
            (Name.SKILL, creation.sha256),
            (creation.name, Digest(creation.sha256)),
        ):
            with (
                self.subTest(name_type=type(name), digest_type=type(digest)),
                self.assertRaises(SkillMutationError),
            ):
                SkillCreation(name, text, digest, creation.size_bytes)

    def test_links_e_fifo(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "SKILL.md"
            source.write_text(creation_text())
            link = root / "link"
            link.symlink_to(source)
            hard = root / "hard"
            os.link(source, hard)
            fifo = root / "fifo"
            os.mkfifo(fifo)
            parent = root / "parent"
            parent.symlink_to(root, target_is_directory=True)
            for path in (
                source,
                hard,
                link,
                fifo,
                parent / "SKILL.md",
                root / ".." / root.name / "SKILL.md",
            ):
                with self.subTest(path=path.name), self.assertRaises(SkillMutationError):
                    read_skill_creation(path)
