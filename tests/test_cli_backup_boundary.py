"""Backup recusa DB não regular e nunca arquiva o SQLite vivo sem WAL."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from kairos_cli.backup import run_backup


class BackupBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.archive = self.root / "backup.tar.gz"

    def run_backup(self, *, output=None):
        stdout, stderr = io.StringIO(), io.StringIO()
        args = argparse.Namespace(output=str(self.archive) if output is None else output, json=True)
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = asyncio.run(run_backup(self.home, args))
        return code, stdout.getvalue(), stderr.getvalue()

    def database(self, path=None):
        connection = sqlite3.connect(path or self.home / "state.db")
        self.addCleanup(connection.close)
        return connection

    def assert_refused(self, result):
        code, output, error = result
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertNotIn(str(self.root), error)
        self.assertFalse(self.archive.exists())
        return error

    def test_symlink_database_refuses_without_copying_external_sentinel(self):
        external = self.root / "external.db"
        source = self.database(external)
        source.execute("CREATE TABLE private(value TEXT)")
        source.execute("INSERT INTO private VALUES ('outside-sentinel-private')")
        source.commit()
        source.close()
        (self.home / "state.db").symlink_to(external)
        error = self.assert_refused(self.run_backup())
        self.assertNotIn("outside-sentinel-private", error)
        self.assertEqual(
            sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*")),
            ["external.db", "home", "home/state.db"],
        )

    def test_internal_and_dangling_symlinks_are_also_refused(self):
        for target in (self.home / "valid.db", self.root / "absent.db"):
            with self.subTest(target=target.name):
                if target.name == "valid.db":
                    connection = self.database(target)
                    connection.execute("CREATE TABLE probe(value TEXT)")
                    connection.commit()
                    connection.close()
                (self.home / "state.db").symlink_to(target)
                self.assert_refused(self.run_backup())
                (self.home / "state.db").unlink()

    def test_fifo_database_refuses_without_blocking(self):
        os.mkfifo(self.home / "state.db")
        code = """
import argparse,asyncio,sys
from pathlib import Path
from kairos_cli.backup import run_backup
raise SystemExit(asyncio.run(run_backup(Path(sys.argv[1]),argparse.Namespace(output=sys.argv[2],json=True))))
"""
        try:
            result = subprocess.run(
                [sys.executable, "-c", code, str(self.home), str(self.archive)],
                capture_output=True,
                timeout=3,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self.fail("Backup bloqueou ao abrir FIFO como SQLite.")
        self.assert_refused((result.returncode, result.stdout.decode(), result.stderr.decode()))

    def test_socket_database_and_directory_refuse(self):
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(self.home / "state.db"))
            self.assert_refused(self.run_backup())
        (self.home / "state.db").unlink()
        (self.home / "state.db").mkdir()
        self.assert_refused(self.run_backup())

    def test_database_created_during_archive_includes_retained_wal(self):
        (self.home / "config.json").write_text("{}")
        original = tarfile.TarFile.add
        created = []

        def create_then_add(bundle, path, *args, **kwargs):
            if kwargs.get("arcname") == "config.json":
                keeper = self.database()
                self.assertEqual(keeper.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
                keeper.execute("PRAGMA wal_autocheckpoint=0")
                keeper.execute("CREATE TABLE probe(value TEXT)")
                keeper.execute("INSERT INTO probe VALUES ('committed-in-wal')")
                keeper.commit()
                self.assertGreater((self.home / "state.db-wal").stat().st_size, 0)
                created.append(keeper)
            return original(bundle, path, *args, **kwargs)

        with patch.object(tarfile.TarFile, "add", create_then_add):
            result, output, error = self.run_backup()
        self.assertEqual(result, 0, error)
        self.assertEqual(len(created), 1)
        with tarfile.open(self.archive, "r:gz") as bundle:
            self.assertIn("state.db", bundle.getnames())
            self.assertFalse(
                {"state.db-wal", "state.db-shm", "state.db-journal"} & set(bundle.getnames())
            )
            snapshot = self.root / "snapshot.db"
            with bundle.extractfile("state.db") as member:
                snapshot.write_bytes(member.read())
            self.assertEqual(
                json.loads(output)["files"], sum(m.isfile() for m in bundle.getmembers())
            )
        with self.database(snapshot) as restored:
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone(), ("ok",))
            self.assertEqual(
                restored.execute("SELECT value FROM probe").fetchall(), [("committed-in-wal",)]
            )

    def test_new_home_without_database_preserves_excludes_and_count(self):
        (self.home / "config.json").write_text("{}")
        (self.home / "empty").mkdir()
        (self.home / "empty").chmod(0o710)
        for excluded in ("__pycache__", ".cache", ".pytest_cache"):
            (self.home / excluded).mkdir()
            (self.home / excluded / "private.bin").write_bytes(b"excluded")
        for sidecar in ("state.db-wal", "state.db-shm", "state.db-journal"):
            (self.home / sidecar).write_bytes(b"ignored")
        (self.home / "alias").symlink_to(self.home / "empty", target_is_directory=True)
        code, output, error = self.run_backup()
        self.assertEqual(code, 0, error)
        with tarfile.open(self.archive) as bundle:
            self.assertEqual(bundle.getnames(), ["config.json", "empty"])
            self.assertEqual(bundle.getmember("empty").mode, 0o710)
        self.assertEqual(json.loads(output)["files"], 1)

    def test_database_changed_or_removed_after_snapshot_refuses_and_discards_partial_archive(self):
        keeper = self.database()
        keeper.execute("CREATE TABLE probe(value TEXT)")
        keeper.commit()
        keeper.close()
        original = tarfile.TarFile.add
        original_database = (self.home / "state.db").read_bytes()
        for action in ("replace", "remove", "symlink"):
            with self.subTest(action=action):
                (self.home / "state.db").write_bytes(original_database)

                def change_after_add(bundle, path, *args, action=action, **kwargs):
                    result = original(bundle, path, *args, **kwargs)
                    if kwargs.get("arcname") == "state.db":
                        (self.home / "state.db").rename(self.home / "retained.db")
                        if action == "replace":
                            (self.home / "state.db").write_bytes(original_database)
                        elif action == "symlink":
                            (self.home / "state.db").symlink_to(self.home / "retained.db")
                    return result

                with patch.object(tarfile.TarFile, "add", change_after_add):
                    self.assert_refused(self.run_backup())
                (self.home / "state.db").unlink(missing_ok=True)
                (self.home / "retained.db").unlink()

    def test_boundary_diagnostic_is_distinct_from_corrupt_database_and_both_cleanup(self):
        (self.home / "state.db").symlink_to(self.root / "absent")
        boundary = self.assert_refused(self.run_backup())
        (self.home / "state.db").unlink()
        (self.home / "state.db").write_bytes(b"private-corrupt-sentinel")
        corruption = self.assert_refused(self.run_backup())
        self.assertNotEqual(boundary, corruption)
        self.assertNotIn("private-corrupt-sentinel", corruption)

    def test_database_created_after_absence_decision_refuses_even_if_removed(self):
        (self.home / "zzz.txt").write_text("last entry")
        original = tarfile.TarFile.add
        for remove in (False, True):
            with self.subTest(remove=remove):

                def create_after_decision(bundle, path, *args, remove=remove, **kwargs):
                    if kwargs.get("arcname") == "zzz.txt":
                        connection = sqlite3.connect(self.home / "state.db")
                        connection.execute("CREATE TABLE probe(value TEXT)")
                        connection.commit()
                        connection.close()
                        if remove:
                            (self.home / "state.db").unlink()
                    return original(bundle, path, *args, **kwargs)

                with patch.object(tarfile.TarFile, "add", create_after_decision):
                    self.assert_refused(self.run_backup())
                (self.home / "state.db").unlink(missing_ok=True)

    def test_default_partial_archive_is_removed_on_failure(self):
        (self.home / "config.json").write_text("{}")
        original = tempfile.mkstemp

        def local_temporary(*args, **kwargs):
            kwargs["dir"] = self.root
            return original(*args, **kwargs)

        with (
            patch("kairos_cli.backup.tempfile.mkstemp", side_effect=local_temporary),
            patch.object(tarfile.TarFile, "add", side_effect=OSError("private backend error")),
        ):
            self.assert_refused(self.run_backup(output=""))
        self.assertEqual(list(self.root.iterdir()), [self.home])

    def test_failed_backup_does_not_remove_preexisting_output(self):
        self.archive.write_bytes(b"previous artifact")
        (self.home / "state.db").write_bytes(b"corrupt")
        code, output, error = self.run_backup()
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertEqual(self.archive.read_bytes(), b"previous artifact")

    def test_output_cannot_replace_source_database(self):
        keeper = self.database()
        keeper.execute("CREATE TABLE probe(value TEXT)")
        keeper.commit()
        keeper.close()
        original = (self.home / "state.db").read_bytes()
        code, output, error = self.run_backup(output=str(self.home / "state.db"))
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertTrue(error)
        self.assertEqual((self.home / "state.db").read_bytes(), original)

    def test_existing_output_inside_home_is_not_included_in_new_backup(self):
        self.archive = self.home / "backup.tar.gz"
        self.archive.write_bytes(b"old-private-backup-sentinel")
        (self.home / "config.json").write_text("{}")
        code, output, error = self.run_backup()
        self.assertEqual(code, 0, error)
        with tarfile.open(self.archive) as bundle:
            self.assertEqual(bundle.getnames(), ["config.json"])
        self.assertEqual(json.loads(output)["files"], 1)

    def test_output_cannot_replace_database_sidecars(self):
        keeper = self.database()
        keeper.execute("PRAGMA journal_mode=WAL")
        keeper.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("CREATE TABLE probe(value TEXT)")
        keeper.execute("INSERT INTO probe VALUES ('retained')")
        keeper.commit()
        for name in ("state.db-wal", "state.db-shm", "state.db-journal"):
            with self.subTest(name=name):
                target = self.home / name
                before = target.read_bytes() if target.exists() else None
                code, output, error = self.run_backup(output=str(target))
                self.assertEqual(code, 1)
                self.assertEqual(output, "")
                self.assertTrue(error)
                self.assertEqual(target.read_bytes() if target.exists() else None, before)
        self.assertEqual(keeper.execute("SELECT value FROM probe").fetchall(), [("retained",)])

    def test_snapshot_preserves_permissions_without_special_mode_bits(self):
        connection = self.database()
        connection.execute("CREATE TABLE probe(value TEXT)")
        connection.commit()
        connection.close()
        (self.home / "state.db").chmod(0o4640)
        code, _output, error = self.run_backup()
        self.assertEqual(code, 0, error)
        with tarfile.open(self.archive) as bundle:
            self.assertEqual(bundle.getmember("state.db").mode, 0o640)

    def test_cleanup_failure_is_reported_without_backend_traceback(self):
        (self.home / "state.db").write_bytes(b"corrupt")
        original = Path.unlink

        def denied(path, *args, **kwargs):
            if path.name.startswith(".kairos-backup-"):
                raise PermissionError("private-cleanup-backend-sentinel")
            return original(path, *args, **kwargs)

        with patch.object(Path, "unlink", denied):
            code, output, error = self.run_backup()
        self.assertEqual(code, 1)
        self.assertEqual(output, "")
        self.assertNotIn("private-cleanup-backend-sentinel", error)
        self.assertNotIn(str(self.root), error)

    def test_symlink_swap_at_descriptor_open_is_boundary_failure(self):
        connection = self.database()
        connection.execute("CREATE TABLE probe(value TEXT)")
        connection.commit()
        connection.close()
        target = self.home / "state.db"
        retained = self.home / "retained.db"
        target.rename(retained)
        target.symlink_to(retained)
        boundary = self.assert_refused(self.run_backup())
        target.unlink()
        retained.rename(target)
        original = os.open

        def replace_before_open(path, *args, **kwargs):
            if path == "state.db":
                target.rename(retained)
                target.symlink_to(retained)
            return original(path, *args, **kwargs)

        with patch("os.open", side_effect=replace_before_open):
            error = self.assert_refused(self.run_backup())
        self.assertEqual(error, boundary)
