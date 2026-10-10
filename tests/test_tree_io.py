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

    def test_entry_budget_bounds_directory_enumeration(self):
        from contextlib import contextmanager

        for index in range(20):
            (self.root / str(index)).touch()
        listed = 0
        listdir = os.listdir
        scandir = os.scandir

        def list_all(fd):
            nonlocal listed
            result = listdir(fd)
            listed += len(result)
            return result

        @contextmanager
        def scan(fd):
            nonlocal listed
            with scandir(fd) as iterator:

                def entries():
                    nonlocal listed
                    for entry in iterator:
                        listed += 1
                        yield entry

                yield entries()

        with (
            patch("kairos_filesystem.tree.os.listdir", side_effect=list_all),
            patch("kairos_filesystem.tree.os.scandir", side_effect=scan),
            self.assertRaises(FilesystemError),
        ):
            capture_tree(self.parent, "tree", limits=TreeLimits(1, 1, 3, 1), include_contents=False)
        self.assertLessEqual(listed, 3)

    def test_restore_mount_subdirectory_before_file_creation(self):
        self._restore_with_mounted_subdirectory(with_file=True)

    def test_restore_mount_empty_subdirectory_before_chmod(self):
        self._restore_with_mounted_subdirectory(with_file=False)

    def _restore_with_mounted_subdirectory(self, *, with_file):
        from kairos_filesystem.tree import observed_mount_id

        sub = self.root / "sub"
        sub.mkdir(mode=0o750)
        if with_file:
            (sub / "file").write_bytes(b"keep")
        snap = self.capture()
        mount = observed_mount_id(self.parent)
        destination = self.home / "copy/sub"

        def observe(fd):
            if destination.exists() and os.fstat(fd).st_ino == destination.stat().st_ino:
                return mount + 1
            return mount

        with (
            patch("kairos_filesystem.tree.observed_mount_id", side_effect=observe),
            self.assertRaises(FilesystemError),
        ):
            restore_tree(self.parent, "copy", snap)
        self.assertFalse((destination / "file").exists())
        self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o700)

    def test_restore_replaced_root_before_open_preserved(self):
        self._restore_with_replaced_directory("copy")

    def test_restore_replaced_subdirectory_before_open_preserved(self):
        self._restore_with_replaced_directory("sub")

    def _restore_with_replaced_directory(self, component):
        (self.root / "sub").mkdir()
        (self.root / "sub/file").write_bytes(b"keep")
        snap = self.capture()
        outside = self.home / "outside"
        outside.mkdir(mode=0o750)
        original = outside.stat()
        mkdir = os.mkdir

        def replace_created(name, mode=0o777, *, dir_fd=None):
            mkdir(name, mode, dir_fd=dir_fd)
            if name == component:
                os.rename(name, "created", src_dir_fd=dir_fd, dst_dir_fd=self.parent)
                os.rename("outside", name, src_dir_fd=self.parent, dst_dir_fd=dir_fd)

        with (
            patch("kairos_filesystem.tree.os.mkdir", side_effect=replace_created),
            self.assertRaises(FilesystemError),
        ):
            restore_tree(self.parent, "copy", snap)
        replaced = self.home / ("copy" if component == "copy" else "copy/sub")
        self.assertEqual(replaced.stat().st_ino, original.st_ino)
        self.assertEqual(list(replaced.iterdir()), [])
        self.assertEqual(stat.S_IMODE(replaced.stat().st_mode), 0o750)

    def test_restore_creation_observation_unavailable_before_mkdir(self):
        snap = self.capture()
        with (
            patch("kairos_filesystem.tree.ctypes.CDLL", side_effect=OSError),
            self.assertRaises(FilesystemError) as caught,
        ):
            restore_tree(self.parent, "copy", snap)
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertFalse((self.home / "copy").exists())

    def test_restore_creation_event_overflow_preserves_empty_directory(self):
        import struct

        (self.root / "file").write_bytes(b"keep")
        snap = self.capture()
        read = os.read

        def overflow(fd, count):
            result = read(fd, count)
            if os.readlink(f"/proc/self/fd/{fd}") == "anon_inode:inotify":
                result += struct.pack("=iIII", -1, 0x4000, 0, 0)
            return result

        with (
            patch("kairos_filesystem.tree.os.read", side_effect=overflow),
            self.assertRaises(FilesystemError),
        ):
            restore_tree(self.parent, "copy", snap)
        self.assertEqual(list((self.home / "copy").iterdir()), [])
        self.assertEqual(stat.S_IMODE((self.home / "copy").stat().st_mode), 0o700)

    def test_cleanup_checks_empty_directory_with_bounded_enumeration(self):
        from contextlib import contextmanager

        snap = self.capture()
        scandir = os.scandir
        scans = 0
        listed = 0

        @contextmanager
        def scan(fd):
            nonlocal scans, listed
            scans += 1
            if scans == 2:
                for index in range(20):
                    (self.root / str(index)).touch()
            with scandir(fd) as iterator:

                def entries():
                    nonlocal listed
                    for entry in iterator:
                        listed += 1
                        yield entry

                yield entries()

        with (
            patch("kairos_filesystem.tree.os.scandir", side_effect=scan),
            self.assertRaises(FilesystemError),
        ):
            delete_verified_tree(self.parent, "tree", snap)
        self.assertEqual(listed, 1)
        self.assertEqual(len(list(self.root.iterdir())), 20)

    def test_restore_refuses_unattested_filesystem_before_mkdir(self):
        import ctypes

        from kairos_filesystem.tree import assert_creation_supported

        snap = self.capture()
        outside = self.home / "outside"
        outside.write_bytes(b"preserve")
        mkdir = os.mkdir
        library = ctypes.CDLL(None, use_errno=True)
        fstatfs = library.fstatfs
        fstatfs.argtypes = (ctypes.c_int, ctypes.c_void_p)
        fstatfs.restype = ctypes.c_int

        class ObservedFilesystem:
            def __init__(self, filesystem_type):
                def observe(fd, buffer):
                    result = fstatfs(fd, buffer)
                    if result == 0:
                        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_long))[0] = filesystem_type
                    return result

                self.fstatfs = observe

            def __getattr__(self, name):
                return getattr(library, name)

        creations = []
        for index, filesystem_type in enumerate(
            (0x6969, 0xFF534D42, 0x65735546, 0x794C7630, 0x12345678)
        ):
            destination = f"copy{index}"
            creations.clear()

            def record_mkdir(name, mode=0o777, *, dir_fd=None):
                creations.append(name)
                mkdir(name, mode, dir_fd=dir_fd)

            with (
                self.subTest(filesystem_type=hex(filesystem_type)),
                patch(
                    "kairos_filesystem.tree.ctypes.CDLL",
                    return_value=ObservedFilesystem(filesystem_type),
                ),
                patch("kairos_filesystem.tree.os.mkdir", side_effect=record_mkdir),
            ):
                with self.assertRaises(FilesystemError) as probe:
                    assert_creation_supported(self.parent)
                self.assertEqual(probe.exception.kind, "unavailable")
                with self.assertRaises(FilesystemError) as caught:
                    restore_tree(self.parent, destination, snap)
                self.assertEqual(caught.exception.kind, "unavailable")
                self.assertFalse((self.home / destination).exists())
                self.assertEqual(creations, [])
                self.assertEqual(outside.read_bytes(), b"preserve")

    def test_restore_attests_supported_filesystem_with_real_fstatfs(self):
        import ctypes

        (self.root / "file").write_bytes(b"keep")
        snap = self.capture()
        library = ctypes.CDLL(None, use_errno=True)
        fstatfs = library.fstatfs
        fstatfs.argtypes = (ctypes.c_int, ctypes.c_void_p)
        fstatfs.restype = ctypes.c_int
        observations = []

        class ObservedFilesystem:
            def __init__(self):
                def observe(fd, buffer):
                    result = fstatfs(fd, buffer)
                    observations.append(result)
                    if result == 0:
                        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_long))[0] = 0x01021994
                    return result

                self.fstatfs = observe

            def __getattr__(self, name):
                return getattr(library, name)

        with patch("kairos_filesystem.tree.ctypes.CDLL", return_value=ObservedFilesystem()):
            restored = restore_tree(self.parent, "copy", snap)
        self.assertEqual(restored.contents, snap.contents)
        self.assertTrue(observations)
        self.assertTrue(all(result == 0 for result in observations))

    def test_creation_capability_probe_has_no_filesystem_writes(self):
        from kairos_filesystem.tree import assert_creation_supported

        before = set(self.home.iterdir())
        creations = []
        mkdir = os.mkdir

        def record(name, mode=0o777, *, dir_fd=None):
            creations.append(name)
            mkdir(name, mode, dir_fd=dir_fd)

        with patch("kairos_filesystem.tree.os.mkdir", side_effect=record):
            assert_creation_supported(self.parent)
        self.assertEqual(creations, [])
        self.assertEqual(set(self.home.iterdir()), before)

    def test_creation_capability_probe_requires_inotify_after_fstatfs(self):
        import ctypes

        from kairos_filesystem.tree import assert_creation_supported

        library = ctypes.CDLL(None, use_errno=True)
        before = set(self.home.iterdir())

        class NoNotifications:
            def __getattr__(self, name):
                if name == "inotify_init1":
                    raise AttributeError(name)
                return getattr(library, name)

        with (
            patch("kairos_filesystem.tree.ctypes.CDLL", return_value=NoNotifications()),
            self.assertRaises(FilesystemError) as caught,
        ):
            assert_creation_supported(self.parent)
        self.assertEqual(caught.exception.kind, "unavailable")
        self.assertEqual(set(self.home.iterdir()), before)

    def test_restore_guard_refuses_before_creation(self):
        (self.root / "file").write_bytes(b"contents")
        snapshot = self.capture()

        def guard():
            raise FilesystemError("conflict", "external chain changed")

        with self.assertRaises(FilesystemError):
            restore_tree(self.parent, "copy", snapshot, guard=guard)
        self.assertFalse((self.home / "copy").exists())

    def test_delete_guard_refuses_before_unlink(self):
        (self.root / "file").write_bytes(b"contents")
        snapshot = self.capture()

        def guard():
            raise FilesystemError("conflict", "external chain changed")

        with self.assertRaises(FilesystemError):
            delete_verified_tree(self.parent, "tree", snapshot, guard=guard)
        self.assertEqual((self.root / "file").read_bytes(), b"contents")

    def test_posix_backslash_filename_roundtrip(self):
        filename = r"legacy\asset.bin"
        source = self.root / filename
        source.write_bytes(b"\x00\xffcontents")
        source.chmod(0o640)
        snapshot = self.capture()
        restored = restore_tree(self.parent, "copy", snapshot)
        target = self.home / "copy" / filename
        self.assertEqual(target.read_bytes(), source.read_bytes())
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(restored.contents, snapshot.contents)
        delete_verified_tree(self.parent, "copy", restored)
        self.assertFalse(target.exists())

    def test_restore_final_verification_does_not_allocate_inflated_contents(self):
        from kairos_filesystem.tree import _read_file

        (self.root / "SKILL.md").write_bytes(b"SKILL.md")
        (self.root / "auxiliary").write_bytes(b"a" * 131072)
        snapshot = self.capture()
        original_fsync = os.fsync
        allocations = []

        def inflate(fd):
            original_fsync(fd)
            if fd == self.parent and (self.home / "copy/SKILL.md").exists():
                (self.home / "copy/SKILL.md").write_bytes(b"x" * 65537)

        def observe(fd, size, *, include_contents):
            if include_contents and size == 65537:
                allocations.append(size)
            return _read_file(fd, size, include_contents=include_contents)

        with (
            patch("kairos_filesystem.tree.os.fsync", side_effect=inflate),
            patch("kairos_filesystem.tree._read_file", side_effect=observe),
            self.assertRaises(FilesystemError),
        ):
            restore_tree(self.parent, "copy", snapshot)
        self.assertEqual(allocations, [])
        self.assertEqual((self.home / "copy/SKILL.md").stat().st_size, 65537)

    def test_restore_owns_contents_when_guard_mutates_caller_mapping(self):
        (self.root / "file").write_bytes(b"original")
        snapshot = self.capture()
        contents = dict(snapshot.contents)

        def mutate_caller():
            target = self.home / "copy/file"
            if target.exists() and target.read_bytes() == b"original":
                snapshot.contents["file"] = b"changed!"

        restored = restore_tree(self.parent, "copy", snapshot, guard=mutate_caller)
        self.assertEqual(snapshot.contents["file"], b"changed!")
        self.assertEqual((self.home / "copy/file").read_bytes(), contents["file"])
        self.assertEqual(restored.contents, contents)
        verify_tree(self.parent, "copy", restored)
        snapshot.contents.clear()
        self.assertEqual(restored.contents, contents)
