"""CLI real: arquivos, SQLite, códigos e metadata sem conteúdo privado."""

import contextvars
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_cli_skill_catalog as catalog_fixtures
import test_skill_mutation_recovery as recovery_fixtures
from skill_mutation_fixtures import creation_text
from test_chat_search_loop import answer_round
from test_chat_tool_approval import mutator_round
from test_interaction_skills import skill_payload

from kairos_cli.main import main
from kairos_providers import CanonicalToolCall
from kairos_skills.catalog import catalog_home_id
from kairos_skills.mutation_contract import SkillMutationState
from kairos_state import connect
from kairos_state.repositories.skill_mutations import SkillMutationRepository

installed = catalog_fixtures.installed


class CliMutationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "private-source.md"
        self.source.write_text(
            creation_text(body="private fictitious body").replace(
                "version:", "author: private fictitious author\nversion:"
            )
        )
        env = patch.dict(os.environ, {"KAIROS_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)

    def command(self, args):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdout", out), patch("sys.stderr", err):
            try:
                code = main(args)
            except SystemExit as exit_error:
                code = exit_error.code
        self.assertNotIn("private fictitious body", out.getvalue() + err.getvalue())
        self.assertNotIn("private fictitious author", out.getvalue() + err.getvalue())
        self.assertNotIn(str(self.root), out.getvalue() + err.getvalue())
        return code, out.getvalue(), err.getvalue()

    def test_cli_add_history_rollback_real(self):
        for as_json in (False, True):
            with self.subTest(as_json=as_json):
                options = ["--json"] if as_json else []
                stdin = io.StringIO("must not consume")
                with patch("sys.stdin", stdin):
                    code, output, _ = self.command(
                        ["skills", "add", "--file", str(self.source), *options]
                    )
                    self.assertEqual(code, 0)
                    db = connect(self.home / "state.db")
                    try:
                        repo = SkillMutationRepository(db, home_id=catalog_home_id(self.home))
                        created = repo.history()[0]
                        self.assertIs(created.state, SkillMutationState.COMMITTED)
                        if as_json:
                            metadata = json.loads(output)
                            self.assertEqual(metadata["id"], created.operation_id)
                            self.assertEqual(metadata["origin"], "user")
                            self.assertEqual(metadata["state"], "committed")
                            self.assertIsNone(metadata["reverts"])
                        else:
                            self.assertIn(created.operation_id, output)
                        self.assertEqual(
                            (self.home / "skills" / created.name / "SKILL.md").read_bytes(),
                            self.source.read_bytes(),
                        )
                        self.assertEqual(self.command(["skills", "history", *options])[0], 0)
                        code, output, _ = self.command(
                            ["skills", "rollback", created.operation_id, *options]
                        )
                        self.assertEqual(code, 0)
                        if as_json:
                            self.assertEqual(json.loads(output)["reverts"], created.operation_id)
                        self.assertFalse((self.home / "skills" / created.name).exists())
                        self.assertEqual(
                            self.command(["skills", "rollback", created.operation_id, *options])[0],
                            0,
                        )
                    finally:
                        db.close()
                self.assertEqual(stdin.tell(), 0)

    def test_options_exit_codes_privacy(self):
        for args in (
            ["skills", "add"],
            ["skills", "add", "--file", "-"],
            ["skills", "add", "--file", str(self.root / "missing")],
            ["skills", "rollback", "../invalid"],
            ["skills", "history", "--name", "../invalid"],
            ["skills", "history", "--limit", "0"],
            ["skills", "history", "--limit", "101"],
            ["skills", "history", "--limit", "invalid"],
        ):
            with self.subTest(args=args[:3]):
                self.assertEqual(self.command(args)[0], 2)
        self.assertFalse((self.home / "state.db").exists())
        for limit in (1, 100):
            code, output, _ = self.command(["skills", "history", "--limit", str(limit), "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), [])
        self.assertFalse((self.home / "state.db").exists())
        self.assertEqual(
            self.command(["skills", "add", "--file", str(self.source), "--json"])[0], 0
        )
        self.assertEqual(self.command(["skills", "add", "--file", str(self.source)])[0], 1)
        db = connect(self.home / "state.db")
        try:
            with db:
                db.execute("DROP TRIGGER skill_mutation_contents_no_update")
                db.execute("UPDATE skill_mutation_contents SET text='corrupt private data'")
        finally:
            db.close()
        self.assertEqual(self.command(["skills", "history", "--json"])[0], 1)

    def test_database_and_home_links_refused(self):
        outside = self.root / "outside.db"
        outside.write_bytes(b"preserve outside")
        database = self.home / "state.db"
        database.symlink_to(outside)
        self.assertEqual(self.command(["skills", "add", "--file", str(self.source)])[0], 1)
        self.assertEqual(self.command(["skills", "history"])[0], 1)
        self.assertEqual(outside.read_bytes(), b"preserve outside")
        database.unlink()
        os.link(outside, database)
        self.assertEqual(self.command(["skills", "history"])[0], 1)
        self.assertEqual(outside.read_bytes(), b"preserve outside")

    def test_unavailable_no_success_or_backend_details(self):
        with patch(
            "kairos_skills.mutation_io.ctypes.CDLL", side_effect=OSError("private backend path")
        ):
            code, _, err = self.command(["skills", "add", "--file", str(self.source)])
        self.assertEqual(code, 69)
        self.assertNotIn("private backend path", err)
        self.assertFalse((self.home / "skills" / "revisar-docs").exists())

    def test_history_does_not_reconcile(self):
        context = __import__("multiprocessing").get_context("spawn")
        # Compor/migrar sem publicação, para o filho preparar de verdade.
        from kairos_state.migrations import migrate

        db = connect(self.home / "state.db")
        migrate(db)
        parent, child = context.Pipe()
        process = context.Process(
            target=recovery_fixtures.stopped_creation,
            args=(self.home, self.source, "prepare", child),
        )
        process.start()
        try:
            self.assertTrue(parent.poll(10))
            operation = parent.recv()
            process.kill()
            process.join(5)
            staged = self.home / ".skill-mutations" / "staging" / operation / "SKILL.md"
            before = [tuple(row) for row in db.execute("SELECT * FROM skill_mutation_events")]
            code, output, _ = self.command(["skills", "history", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)[0]["state"], "prepared")
            self.assertEqual(
                [tuple(row) for row in db.execute("SELECT * FROM skill_mutation_events")], before
            )
            self.assertEqual(staged.read_bytes(), self.source.read_bytes())
            self.assertFalse((self.home / "skills" / "revisar-docs").exists())
        finally:
            db.close()
            if process.is_alive():
                process.kill()
                process.join()
            parent.close()
            child.close()

    def test_new_home_and_readonly_history(self):
        new_home = self.root / "new" / "home"
        with patch.dict(os.environ, {"KAIROS_HOME": str(new_home)}):
            code, output, _ = self.command(["skills", "history", "--json"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output), [])
            self.assertFalse(new_home.exists())
            self.assertEqual(
                self.command(["skills", "add", "--file", str(self.source), "--json"])[0], 0
            )
            self.assertEqual(new_home.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                (new_home / "skills" / "revisar-docs" / "SKILL.md").read_bytes(),
                self.source.read_bytes(),
            )

    def test_sqlite_sidecar_link_is_not_opened(self):
        outside = self.root / "outside"
        outside.write_text("preserved")
        (self.home / "state.db-wal").symlink_to(outside)
        self.assertEqual(self.command(["skills", "add", "--file", str(self.source)])[0], 1)
        self.assertEqual(outside.read_text(), "preserved")
        self.assertFalse((self.home / "state.db").exists())

    def test_no_inherited_autonomous_origin(self):
        from kairos_domain.ownership import Actor

        context = contextvars.ContextVar("skill_actor", default=Actor.USER_FOREGROUND)
        token = context.set(Actor.BACKGROUND_REVIEW)
        self.source.write_text(
            creation_text().replace(
                "---\nProcedimento", "metadata:\n  kairos:\n    origin: sediment\n---\nProcedimento"
            )
        )
        try:
            with patch.dict(
                os.environ, {"KAIROS_ORIGIN": "sediment", "KAIROS_ACTOR": "background_review"}
            ):
                code, output, _ = contextvars.copy_context().run(
                    self.command, ["skills", "add", "--file", str(self.source), "--json"]
                )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["origin"], "user")
        finally:
            context.reset(token)


def test_created_skill_reaches_adapter(installed, tmp_path, monkeypatch, capsys):
    source = tmp_path / "authored.md"
    literal = creation_text(body="Conteúdo fictício criado pela CLI á🙂.")
    source.write_text(literal)
    stdin = io.StringIO("never consume")
    monkeypatch.setattr("sys.stdin", stdin)
    assert main(["skills", "add", "--file", str(source), "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    gateway = installed(read=False)
    assert (
        main(
            [
                "run",
                "--session",
                "explicit-new",
                "--skill",
                "revisar-docs",
                "--no-experiences",
                "pedido",
            ]
        )
        == 0
    )
    assert skill_payload(gateway.requests[0].messages[-1])[0][0]["text"] == literal
    gateway = installed()
    assert (
        main(["run", "--session", "catalog-new", "--skills-catalog", "--no-experiences", "pedido"])
        == 0
    )
    tool_message = next(
        message for message in gateway.requests[-1].messages if message.role == "tool"
    )
    assert json.loads(tool_message.content[0].value)["text"] == literal
    assert stdin.tell() == 0
    with connect(tmp_path / "state.db") as db:
        repo = SkillMutationRepository(db, home_id=catalog_home_id(tmp_path))
        assert repo.get(created["id"]).state is SkillMutationState.COMMITTED
    db.close()
    assert literal not in capsys.readouterr().err


def test_existing_catalog_and_cached_page_survive_rollback(installed, tmp_path, capsys):
    source = tmp_path / "authored.md"
    original = creation_text(body="Página original fictícia.")
    source.write_text(original)
    assert main(["skills", "add", "--file", str(source), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    gateway_a = installed()
    assert main(["run", "--session", "a", "--skills-catalog", "--no-experiences", "pedido"]) == 0
    index_a = gateway_a.requests[0].messages[0].content[0].value
    capsys.readouterr()
    second_text = creation_text(name="second-skill", body="Segunda página fictícia.")
    source.write_text(second_text)
    assert main(["skills", "add", "--file", str(source), "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    gateway_b = installed()
    gateway_b.rounds = iter(
        [
            mutator_round(
                CanonicalToolCall("read-second", "skill_view", '{"name":"second-skill"}')
            ),
            answer_round(),
        ]
    )
    assert main(["run", "--session", "b", "--skills-catalog", "--no-experiences", "pedido"]) == 0
    index_b = gateway_b.requests[0].messages[0].content[0].value
    assert "revisar-docs" in index_b and "second-skill" in index_b
    assert main(["skills", "rollback", second["id"], "--json"]) == 0
    capsys.readouterr()
    gateway_c = installed(read=False)
    assert main(["run", "--session", "c", "--skills-catalog", "--no-experiences", "pedido"]) == 0
    assert "revisar-docs" in gateway_c.requests[0].messages[0].content[0].value
    assert "second-skill" not in gateway_c.requests[0].messages[0].content[0].value
    gateway_b = installed()
    gateway_b.rounds = iter(
        [
            mutator_round(
                CanonicalToolCall("cached-second", "skill_view", '{"name":"second-skill"}')
            ),
            answer_round(),
        ]
    )
    assert main(["run", "--session", "b", "--skills-catalog", "--no-experiences", "pedido"]) == 0
    assert gateway_b.requests[0].messages[0].content[0].value == index_b
    messages = [
        json.loads(message.content[0].value)
        for message in gateway_b.requests[-1].messages
        if message.role == "tool"
    ]
    assert messages[-1]["text"] == second_text
    assert main(["skills", "rollback", first["id"], "--json"]) == 0
    gateway_a = installed()
    assert main(["run", "--session", "a", "--skills-catalog", "--no-experiences", "pedido"]) == 0
    assert gateway_a.requests[0].messages[0].content[0].value == index_a
    messages = [
        json.loads(message.content[0].value)
        for message in gateway_a.requests[-1].messages
        if message.role == "tool"
    ]
    assert messages[-1]["text"] == original
