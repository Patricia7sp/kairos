"""Remoção real sem carregar plugins e preservação de provas após morte."""

import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kairos_plugins.removal import PluginRemovalError, remove_plugin
from kairos_plugins.removal_records import read_metadata, read_terminal


class PluginRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.target = self.home / "plugins/Test-plugin"
        self.target.mkdir(parents=True)
        (self.target / "plugin.yaml").write_bytes(b"broken: [")
        (self.target / "a.bin").write_bytes(b"\x00\xffdata")
        (self.target / "z.bin").write_bytes(b"last")

    def test_remove_broken_plugin_without_import(self):
        sentinel = self.home / "sentinel"
        (self.target / "__init__.py").write_text(
            f"from pathlib import Path\nPath({str(sentinel)!r}).touch()\nraise RuntimeError('loaded')"
        )
        result = remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertTrue(result.removed)
        self.assertTrue(result.requires_restart)
        self.assertFalse(self.target.exists())
        self.assertFalse(sentinel.exists())
        operation = self.home / ".plugins-retired" / result.operation_id
        self.assertEqual(operation.stat().st_mode & 0o777, 0o700)
        self.assertEqual((operation / "metadata.json").stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads((operation / "result.json").read_text())["state"], "removed")
        again = remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual(again, result)

    def test_preserves_data_config_other_and_bundle(self):
        preserve = [
            "plugin-data/Test-plugin/data",
            "config.yaml",
            "plugins/other/data",
            "bundled/Test-plugin/data",
        ]
        for name in preserve:
            path = self.home / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"preserved")
        remove_plugin(self.home, self.target.name, confirmed=True)
        for name in preserve:
            self.assertEqual((self.home / name).read_bytes(), b"preserved")

    def test_git_file_does_not_follow_pointer(self):
        outside = self.home / "external-git"
        outside.mkdir()
        (outside / "data").write_bytes(b"keep")
        (self.target / ".git").write_text(f"gitdir: {outside}\n")
        remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual((outside / "data").read_bytes(), b"keep")

    def test_confirmation_before_any_io(self):
        with (
            patch("kairos_plugins.removal.open_directory", side_effect=AssertionError("I/O")),
            patch("kairos_plugins.removal.filesystem_lock", side_effect=AssertionError("lock")),
            self.assertRaises(PluginRemovalError) as raised,
        ):
            remove_plugin(self.home / "absent", "ok", confirmed=False)
        self.assertEqual(raised.exception.kind, "confirmation")
        self.assertFalse((self.home / "absent").exists())

    def test_bad_name(self):
        for name in ("", "..", "../x", "/x", "a..b", "a/b", "a\\b", "a\nsecret", "a" * 65):
            with self.subTest(name=name), self.assertRaises(PluginRemovalError) as raised:
                remove_plugin(self.home, name, confirmed=True)
            self.assertEqual(raised.exception.kind, "input")
        self.assertFalse((self.home / ".plugins-write.lock").exists())

    def test_absent_name_without_proof_is_error(self):
        with self.assertRaises(PluginRemovalError):
            remove_plugin(self.home, "absent", confirmed=True)

    def test_partial_cleanup_reports_retired_id(self):
        original = os.unlink

        def fail(name, *, dir_fd=None):
            if name == "plugin.yaml":
                raise OSError("SECRET backend")
            return original(name, dir_fd=dir_fd)

        with (
            patch("kairos_filesystem.tree.os.unlink", side_effect=fail),
            self.assertRaises(PluginRemovalError) as raised,
        ):
            remove_plugin(self.home, self.target.name, confirmed=True)
        error = raised.exception
        self.assertTrue(error.retired)
        self.assertNotIn("SECRET", str(error))
        operation = self.home / ".plugins-retired" / error.operation_id
        self.assertTrue((operation / "tree/plugin.yaml").exists())
        self.assertFalse((operation / "result.json").exists())
        with self.assertRaises(PluginRemovalError) as repeated:
            remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual(repeated.exception.operation_id, error.operation_id)
        self.assertTrue((operation / "tree/plugin.yaml").exists())

    def test_replaced_descendant_preserved(self):
        from kairos_filesystem.tree import delete_verified_tree

        def replace(parent, name, expected, **kwargs):
            tree = Path(f"/proc/self/fd/{parent}") / name
            (tree / "a.bin").rename(tree / "old")
            (tree / "a.bin").write_bytes(b"replacement")
            delete_verified_tree(parent, name, expected, **kwargs)

        with (
            patch("kairos_plugins.removal.delete_verified_tree", side_effect=replace),
            self.assertRaises(PluginRemovalError) as raised,
        ):
            remove_plugin(self.home, self.target.name, confirmed=True)
        operation = self.home / ".plugins-retired" / raised.exception.operation_id
        self.assertEqual((operation / "tree/a.bin").read_bytes(), b"replacement")

    def test_unsafe_target_rejected(self):
        outside = self.home / "outside"
        outside.write_bytes(b"keep")
        for kind in ("symlink", "hardlink", "fifo"):
            child = self.target / "unsafe"
            if kind == "symlink":
                child.symlink_to(outside)
            elif kind == "hardlink":
                os.link(outside, child)
            else:
                os.mkfifo(child)
            with self.subTest(kind=kind), self.assertRaises(PluginRemovalError):
                remove_plugin(self.home, self.target.name, confirmed=True)
            self.assertTrue(self.target.exists())
            self.assertEqual(outside.read_bytes(), b"keep")
            child.unlink()

    def _death(self, boundary):
        code = """
import os, sys
from pathlib import Path
from unittest.mock import patch
import kairos_plugins.removal as removal
import kairos_filesystem.tree as tree
boundary = sys.argv[2]
signal = int(sys.argv[3])
def halt():
    os.write(signal, b'ready')
    import signal as signals
    while True: signals.pause()
if boundary in ('before_rename', 'after_rename'):
    original = removal.rename_no_replace
    def stop(*args, **kwargs):
        if boundary == 'after_rename': original(*args, **kwargs)
        halt()
    target = 'kairos_plugins.removal.rename_no_replace'
elif boundary == 'during_cleanup':
    original = tree.os.unlink
    def stop(*args, **kwargs):
        original(*args, **kwargs)
        halt()
    target = 'kairos_filesystem.tree.os.unlink'
else:
    def stop(*args, **kwargs): halt()
    target = 'kairos_plugins.removal.write_terminal'
with patch(target, side_effect=stop):
    removal.remove_plugin(Path(sys.argv[1]), 'Test-plugin', confirmed=True)
"""
        reader, writer = os.pipe()
        try:
            process = subprocess.Popen(
                [sys.executable, "-c", code, str(self.home), boundary, str(writer)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                pass_fds=(writer,),
            )
            os.close(writer)
            writer = None
            try:
                ready, _, _ = select.select([reader], [], [], 15)
                self.assertTrue(ready, "subprocesso não atingiu a fronteira")
                marker = os.read(reader, 5)
                if marker != b"ready":
                    process.kill()
                    _, error = process.communicate(timeout=5)
                    self.fail(error.decode())
                process.kill()
                _, error = process.communicate(timeout=5)
                self.assertEqual(process.returncode, -9, error.decode())
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
        finally:
            os.close(reader)
            if writer is not None:
                os.close(writer)
        operations = list((self.home / ".plugins-retired").iterdir())
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        descriptor = os.open(operation, os.O_RDONLY | os.O_DIRECTORY)
        try:
            name, operation_id, capture = read_metadata(descriptor)
            self.assertEqual(name, self.target.name)
            self.assertEqual(operation_id, operation.name)
            self.assertIsNone(capture.contents)
            self.assertIsNone(read_terminal(descriptor))
        finally:
            os.close(descriptor)
        return operation

    def test_death_before_rename(self):
        operation = self._death("before_rename")
        self.assertTrue(self.target.exists())
        result = remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertTrue(result.removed)
        self.assertEqual(json.loads((operation / "result.json").read_text())["state"], "aborted")
        self.assertNotEqual(result.operation_id, operation.name)

    def test_death_after_rename(self):
        self._assert_dead_residue("after_rename", True)

    def test_death_during_cleanup(self):
        self._assert_dead_residue("during_cleanup", True)

    def test_death_before_terminal_result(self):
        self._assert_dead_residue("before_terminal", False)

    def _assert_dead_residue(self, boundary, residue):
        operation = self._death(boundary)
        self.assertFalse(self.target.exists())
        self.assertEqual((operation / "tree").exists(), residue)
        before = sorted(p.name for p in (operation / "tree").iterdir()) if residue else []
        with self.assertRaises(PluginRemovalError) as raised:
            remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual(raised.exception.operation_id, operation.name)
        self.assertTrue(raised.exception.retired)
        self.assertFalse((operation / "result.json").exists())
        if residue:
            self.assertEqual(sorted(p.name for p in (operation / "tree").iterdir()), before)

    def test_reinstall_is_new_operation(self):
        first = remove_plugin(self.home, self.target.name, confirmed=True)
        self.target.mkdir()
        (self.target / "fresh").write_bytes(b"new")
        second = remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertNotEqual(first.operation_id, second.operation_id)
        self.assertFalse(self.target.exists())

    def test_unknown_private_directory_preserved(self):
        unknown = self.home / ".plugins-retired/unknown"
        unknown.mkdir(parents=True, mode=0o700)
        unknown.parent.chmod(0o700)
        (unknown / "keep").write_bytes(b"unknown")
        remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual((unknown / "keep").read_bytes(), b"unknown")

    def test_corrupt_result_never_proves_success(self):
        result = remove_plugin(self.home, self.target.name, confirmed=True)
        terminal = self.home / ".plugins-retired" / result.operation_id / "result.json"
        terminal.write_text('{"version":1,"state":"removed"}')
        with self.assertRaises(PluginRemovalError):
            remove_plugin(self.home, self.target.name, confirmed=True)

    def test_metadata_is_exclusive_and_has_no_contents(self):
        from kairos_plugins.removal_records import write_metadata, write_terminal

        result = remove_plugin(self.home, self.target.name, confirmed=True)
        operation = self.home / ".plugins-retired" / result.operation_id
        original = (operation / "metadata.json").read_bytes()
        self.assertNotIn(b"contents", original)
        descriptor = os.open(operation, os.O_RDONLY | os.O_DIRECTORY)
        try:
            name, operation_id, capture = read_metadata(descriptor)
            with self.assertRaises(FileExistsError):
                write_metadata(descriptor, name, operation_id, capture)
            with self.assertRaises(FileExistsError):
                write_terminal(descriptor, state="aborted")
        finally:
            os.close(descriptor)
        self.assertEqual((operation / "metadata.json").read_bytes(), original)

    def test_fsync_failure_after_rename_reports_retired(self):
        from kairos_filesystem.descriptors import rename_no_replace

        def move_then_fail(*args):
            rename_no_replace(*args)
            raise OSError("SECRET fsync")

        with (
            patch("kairos_plugins.removal.rename_no_replace", side_effect=move_then_fail),
            self.assertRaises(PluginRemovalError) as raised,
        ):
            remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertTrue(raised.exception.retired)
        self.assertFalse(self.target.exists())
        self.assertTrue(
            (self.home / ".plugins-retired" / raised.exception.operation_id / "tree").exists()
        )

    def test_replaced_external_ancestor_stops_cleanup_and_preserves_residue(self):
        original = os.unlink
        moved = self.home / "moved-proofs"
        replaced = False

        def replace(name, *, dir_fd=None):
            nonlocal replaced
            original(name, dir_fd=dir_fd)
            if not replaced:
                replaced = True
                proofs = self.home / ".plugins-retired"
                proofs.rename(moved)
                proofs.mkdir(mode=0o700)

        with (
            patch("kairos_filesystem.tree.os.unlink", side_effect=replace),
            self.assertRaises(PluginRemovalError) as raised,
        ):
            remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual(raised.exception.kind, "conflict")
        self.assertTrue(raised.exception.retired)
        tree = moved / raised.exception.operation_id / "tree"
        self.assertTrue(tree.is_dir())
        self.assertGreater(len(list(tree.iterdir())), 0)
        self.assertFalse((tree.parent / "result.json").exists())

    def test_uuid_collision_preserves_unknown_orphan(self):
        import uuid

        operation_id = "da0b2c50-806d-4e8f-9ef9-c0305f652706"
        orphan = self.home / ".plugins-retired" / operation_id
        orphan.mkdir(parents=True, mode=0o700)
        orphan.parent.chmod(0o700)
        (orphan / "unknown").write_bytes(b"preserve")
        with (
            patch("kairos_plugins.removal.uuid.uuid4", return_value=uuid.UUID(operation_id)),
            self.assertRaises(PluginRemovalError),
        ):
            remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual((orphan / "unknown").read_bytes(), b"preserve")
        self.assertEqual(sorted(p.name for p in orphan.iterdir()), ["unknown"])
        self.assertTrue(self.target.exists())

    def test_terminal_fsync_failure_is_retried_and_still_refuses_success(self):
        self._assert_terminal_fsync_failure("file")

    def test_terminal_directory_fsync_failure_is_retried(self):
        self._assert_terminal_fsync_failure("directory")

    def _assert_terminal_fsync_failure(self, kind):
        original = os.fsync
        attempts = 0

        def fail_terminal(fd):
            nonlocal attempts
            path = Path(os.readlink(f"/proc/self/fd/{fd}"))
            terminal = (
                path.name == "result.json"
                if kind == "file"
                else path.is_dir() and (path / "result.json").exists()
            )
            if terminal:
                attempts += 1
                raise OSError("SECRET terminal fsync")
            return original(fd)

        with patch("kairos_plugins.removal_records.os.fsync", side_effect=fail_terminal):
            with self.assertRaises(PluginRemovalError) as first:
                remove_plugin(self.home, self.target.name, confirmed=True)
            terminal = self.home / ".plugins-retired" / first.exception.operation_id / "result.json"
            before = terminal.read_bytes()
            with self.assertRaises(PluginRemovalError) as repeated:
                remove_plugin(self.home, self.target.name, confirmed=True)
            self.assertEqual(repeated.exception.operation_id, first.exception.operation_id)
            self.assertTrue(repeated.exception.retired)
            self.assertEqual(attempts, 2)
            self.assertEqual(terminal.read_bytes(), before)
        recovered = remove_plugin(self.home, self.target.name, confirmed=True)
        self.assertEqual(recovered.operation_id, first.exception.operation_id)
        self.assertTrue(recovered.removed)
