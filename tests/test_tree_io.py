"""Árvores reais: captura, restauração exclusiva e exclusão conferida."""

import os
import stat
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from kairos_filesystem.contract import FilesystemError, TreeCapture, TreeLimits
from kairos_filesystem.lock import filesystem_lock
from kairos_filesystem.tree import capture_tree, delete_verified_tree, restore_tree, verify_tree


class TreeIOTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.parent = os.open(self.home, os.O_RDONLY | os.O_DIRECTORY)
        self.addCleanup(os.close, self.parent)
        self.root = self.home / "tree"
        self.root.mkdir()
        self.limits = TreeLimits(1024 * 1024, 1024 * 1024, 50, 8)

    def capture(self, **kwargs):
        return capture_tree(
            self.parent, "tree", limits=self.limits, include_contents=True, **kwargs
        )

    def test_binary_hidden_empty_modes_restore_new_identity(self):
        (self.root / ".binary").write_bytes(b"\x00\xff\r\n")
        (self.root / ".binary").chmod(0o640)
        (self.root / "empty").mkdir(mode=0o750)
        before = self.capture()
        after = restore_tree(self.parent, "copy", before)
        self.assertEqual(before.contents, after.contents)
        self.assertEqual(
            [(e.path, e.mode) for e in before.entries], [(e.path, e.mode) for e in after.entries]
        )
        self.assertNotEqual(
            (before.entries[0].device, before.entries[0].inode),
            (after.entries[0].device, after.entries[0].inode),
        )
        self.assertTrue((self.home / "copy/empty").is_dir())
        self.assertEqual(stat.S_IMODE((self.home / "copy/.binary").stat().st_mode), 0o640)

    def test_stream_inventory_and_delete(self):
        (self.root / "file").write_bytes(b"hello")
        (self.root / "empty").mkdir()
        snapshot = capture_tree(self.parent, "tree", limits=self.limits, include_contents=False)
        self.assertIsNone(snapshot.contents)
        verify_tree(self.parent, "tree", snapshot)
        delete_verified_tree(self.parent, "tree", snapshot)
        self.assertFalse(self.root.exists())

    def test_restore_occupied_preserved(self):
        before = self.capture()
        with self.assertRaises(FilesystemError):
            restore_tree(self.parent, "tree", before)
        self.assertEqual(before, self.capture())

    def test_links_and_fifo_preserve_external(self):
        outside = self.home / "outside"
        outside.write_bytes(b"keep")
        child = self.root / "child"
        for kind in ("symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind):
                if kind == "symlink":
                    child.symlink_to(outside)
                elif kind == "hardlink":
                    os.link(outside, child)
                else:
                    os.mkfifo(child)
                with self.assertRaises(FilesystemError):
                    self.capture()
                self.assertEqual(outside.read_bytes(), b"keep")
                child.unlink()

    def test_inclusive_limits_and_plus_one(self):
        (self.root / "sub").mkdir()
        (self.root / "sub/file").write_bytes(b"abcd")
        exact = TreeLimits(4, 4, 3, 2)
        capture_tree(self.parent, "tree", limits=exact, include_contents=True)
        for limits in (
            replace(exact, total_bytes=3),
            replace(exact, file_bytes=3),
            replace(exact, entries=2),
            replace(exact, depth=1),
        ):
            with self.subTest(limits=limits), self.assertRaises(FilesystemError):
                capture_tree(self.parent, "tree", limits=limits, include_contents=True)

    def test_bind_mount_identity_rejected(self):
        from kairos_filesystem.tree import observed_mount_id

        root_id = observed_mount_id(self.parent)
        with (
            patch("kairos_filesystem.tree.observed_mount_id", side_effect=[root_id, root_id + 1]),
            self.assertRaises(FilesystemError),
        ):
            self.capture()
        (self.root / "child").write_bytes(b"keep")
        with (
            patch(
                "kairos_filesystem.tree.observed_mount_id",
                side_effect=[root_id, root_id, root_id + 1],
            ),
            self.assertRaises(FilesystemError),
        ):
            self.capture()

    def test_mount_observation_unavailable(self):
        with (
            patch(
                "kairos_filesystem.tree.observed_mount_id",
                side_effect=FilesystemError("unavailable", "indisponível"),
            ),
            self.assertRaises(FilesystemError) as caught,
        ):
            self.capture()
        self.assertEqual(caught.exception.kind, "unavailable")

    def test_replaced_child_preserved(self):
        child = self.root / "child"
        child.write_bytes(b"before")
        snapshot = self.capture()
        child.rename(self.home / "original")
        child.write_bytes(b"before")
        with self.assertRaises(FilesystemError):
            delete_verified_tree(self.parent, "tree", snapshot)
        self.assertEqual(child.read_bytes(), b"before")
        self.assertEqual((self.home / "original").read_bytes(), b"before")

    def test_edit_during_read_refused(self):
        child = self.root / "child"
        child.write_bytes(b"before")
        real_read = os.read
        changed = False

        def change(fd, count):
            nonlocal changed
            data = real_read(fd, count)
            if not changed:
                changed = True
                child.write_bytes(b"edited")
            return data

        with (
            patch("kairos_filesystem.tree.os.read", side_effect=change),
            self.assertRaises(FilesystemError),
        ):
            self.capture()

    def test_acl_xattr_and_special_modes_rejected(self):
        child = self.root / "child"
        child.write_bytes(b"keep")
        child.chmod(0o4644)
        with self.assertRaises(FilesystemError):
            self.capture()
        child.chmod(0o644)
        os.setxattr(child, "user.fixture", b"metadata")
        with self.assertRaises(FilesystemError):
            self.capture()
        os.removexattr(child, "user.fixture")
        with (
            patch("kairos_filesystem.tree.os.listxattr", return_value=["system.posix_acl_access"]),
            self.assertRaises(FilesystemError),
        ):
            self.capture()

    def test_rename_ctime_not_frozen(self):
        snapshot = self.capture()
        os.rename("tree", "retired", src_dir_fd=self.parent, dst_dir_fd=self.parent)
        verify_tree(self.parent, "retired", snapshot)
        delete_verified_tree(self.parent, "retired", snapshot)
        self.assertFalse((self.home / "retired").exists())

    def test_invalid_snapshot_refused_before_creation(self):
        (self.root / "child").write_bytes(b"keep")
        snap = self.capture()
        cases = [
            TreeCapture(snap.entries, None),
            replace(snap, contents={"child": b"bad"}),
            replace(snap, entries=(snap.entries[0], replace(snap.entries[1], path="../outside"))),
            replace(snap, entries=(*snap.entries, snap.entries[1])),
            replace(snap, entries=(replace(snap.entries[0], mode=0o4755), *snap.entries[1:])),
        ]
        for snapshot in cases:
            with self.subTest(snapshot=snapshot), self.assertRaises(FilesystemError):
                restore_tree(self.parent, "copy", snapshot)
            self.assertFalse((self.home / "copy").exists())

    def test_lock_deadline_and_unsafe_entry(self):
        locked = threading.Event()
        release = threading.Event()

        def owner():
            with filesystem_lock(self.home, ".test.lock"):
                locked.set()
                release.wait(2)

        thread = threading.Thread(target=owner)
        thread.start()
        try:
            self.assertTrue(locked.wait(1))
            start = time.monotonic()
            with (
                self.assertRaises(FilesystemError),
                filesystem_lock(self.home, ".test.lock", timeout=0.05),
            ):
                self.fail("lock concorrente")
            self.assertLess(time.monotonic() - start, 0.5)
            with filesystem_lock(self.home, ".other.lock", timeout=0):
                pass
        finally:
            release.set()
            thread.join(2)
        (self.home / ".test.lock").unlink()
        (self.home / ".test.lock").symlink_to(self.home / "outside")
        with self.assertRaises(FilesystemError), filesystem_lock(self.home, ".test.lock"):
            pass
        self.assertFalse((self.home / "outside").exists())

    def test_cleanup_preserves_child_replaced_after_first_unlink(self):
        (self.root / "a").write_bytes(b"original")
        (self.root / "z").write_bytes(b"last")
        snap = self.capture()
        unlink = os.unlink
        changed = False

        def replace_next(name, *, dir_fd=None):
            nonlocal changed
            unlink(name, dir_fd=dir_fd)
            if not changed:
                changed = True
                (self.root / "a").rename(self.home / "saved")
                (self.root / "a").write_bytes(b"replacement")

        with (
            patch("kairos_filesystem.tree.os.unlink", side_effect=replace_next),
            self.assertRaises(FilesystemError),
        ):
            delete_verified_tree(self.parent, "tree", snap)
        self.assertEqual((self.root / "a").read_bytes(), b"replacement")
        self.assertEqual((self.home / "saved").read_bytes(), b"original")

    def test_cleanup_preserves_child_edited_after_first_unlink(self):
        (self.root / "a").write_bytes(b"original")
        (self.root / "z").write_bytes(b"last")
        snap = self.capture()
        unlink = os.unlink

        def edit_next(name, *, dir_fd=None):
            unlink(name, dir_fd=dir_fd)
            (self.root / "a").write_bytes(b"edited")

        with (
            patch("kairos_filesystem.tree.os.unlink", side_effect=edit_next),
            self.assertRaises(FilesystemError),
        ):
            delete_verified_tree(self.parent, "tree", snap)
        self.assertEqual((self.root / "a").read_bytes(), b"edited")

    def test_special_file_rejected_before_open(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        opened = []
        real_open = os.open

        def record(name, flags, mode=0o777, *, dir_fd=None):
            opened.append(name)
            return real_open(name, flags, mode, dir_fd=dir_fd)

        with (
            patch("kairos_filesystem.tree.os.open", side_effect=record),
            self.assertRaises(FilesystemError),
        ):
            self.capture()
        self.assertNotIn("fifo", opened)

    def test_mount_reader_unavailable_is_domain_error(self):
        from kairos_filesystem.tree import observed_mount_id

        with (
            patch("kairos_filesystem.tree.open", side_effect=PermissionError),
            self.assertRaises(FilesystemError) as caught,
        ):
            observed_mount_id(self.parent)
        self.assertEqual(caught.exception.kind, "unavailable")

    def test_invalid_limits_and_name_before_write(self):
        snap = self.capture()
        for name in ("../escaped", "/absolute", "", ".", "..", "a/b"):
            with self.subTest(name=name), self.assertRaises(FilesystemError):
                restore_tree(self.parent, name, snap)
        with self.assertRaises(FilesystemError):
            capture_tree(
                self.parent, "tree", limits=TreeLimits(True, 1, 1, 1), include_contents=True
            )
        for timeout in (True, -1, 5.1, float("nan")):
            with (
                self.subTest(timeout=timeout),
                self.assertRaises(FilesystemError),
                filesystem_lock(self.home, "unused", timeout=timeout),
            ):
                pass
        self.assertFalse((self.home / "unused").exists())

    def test_bind_mount_replaced_after_read_is_reobserved(self):
        from kairos_filesystem.tree import observed_mount_id

        (self.root / "child").write_bytes(b"keep")
        real_read = os.read
        real_open = os.open
        mount = observed_mount_id(self.parent)
        changed = False
        reopened = set()

        def read(fd, count):
            nonlocal changed
            result = real_read(fd, count)
            changed = True
            return result

        def open_entry(name, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(name, flags, mode, dir_fd=dir_fd)
            if changed:
                reopened.add(fd)
            return fd

        def observe(fd):
            return mount + 1 if fd in reopened else mount

        with (
            patch("kairos_filesystem.tree.os.read", side_effect=read),
            patch("kairos_filesystem.tree.os.open", side_effect=open_entry),
            patch("kairos_filesystem.tree.observed_mount_id", side_effect=observe),
            self.assertRaises(FilesystemError),
        ):
            self.capture()
        self.assertEqual((self.root / "child").read_bytes(), b"keep")
