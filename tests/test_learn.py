"""Aprendizado: intenção explícita e autoria com capacidade limitada."""

import unittest


class LearnPromptTests(unittest.TestCase):
    def test_exact_command_and_empty_request(self):
        from kairos_skills.learn import parse_learn_request

        self.assertEqual(parse_learn_request("/learn  revisar commits "), "revisar commits")
        self.assertIsNone(parse_learn_request("/learning algo"))
        self.assertIsNone(parse_learn_request("texto /learn algo"))
        with self.assertRaises(ValueError):
            parse_learn_request("/learn  ")

    def test_prompt_is_deterministic_and_keeps_user_request(self):
        from kairos_skills.learn import build_learn_prompt

        request = "Transforme a revisão anterior em procedimento."
        prompt = build_learn_prompt(request)
        self.assertEqual(prompt, build_learn_prompt(request))
        self.assertIn(request, prompt)
        self.assertIn("skill_create", prompt)
        self.assertIn("/learn", prompt)
        self.assertIn("esclarecimento", prompt)


class LearnCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir()
        self.env = patch.dict(os.environ, {"KAIROS_ALLOW_TOOLS_IN_TESTS": "1"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def turn(self):
        from kairos_integration.learn_turn import LearnTurn

        return LearnTurn(
            home=self.home, session_id="session", request="aprenda", guard=lambda: None
        )

    def call(self, name="revisar-docs", raw=None):
        import json

        from skill_mutation_fixtures import creation_text

        from kairos_providers import CanonicalToolCall

        return CanonicalToolCall(
            id="call",
            name="skill_create",
            arguments=raw
            if raw is not None
            else json.dumps({"name": name, "skill_md": creation_text(name)}),
        )

    async def test_commit_closes_capability_and_uses_existing_ledger(self):
        from contextvars import copy_context

        from kairos_skills.mutation_contract import SkillMutationError
        from kairos_skills.removal_state import database_connection, read_skill_repository
        from kairos_tools.skill_create import skill_create_tool, skill_creation_scope

        turn = self.turn()
        with skill_creation_scope(turn.capability):
            retained = copy_context()
            proposal, future = turn.propose(self.call())
            with self.assertRaises(SkillMutationError):
                turn.propose(self.call("another"))
            turn.decide(proposal.id, "session", proposal.sha256, "criar revisar-docs")
            self.assertEqual(await future, "allow")
            receipt = await skill_create_tool(proposal.name, proposal.skill_md)
            self.assertEqual(receipt["state"], "committed")
            self.assertEqual(receipt["origin"], "user")
            self.assertEqual(receipt["sha256"], proposal.sha256)
            self.assertEqual(
                (self.home / "skills/revisar-docs/SKILL.md").read_text(), proposal.skill_md
            )
            self.assertIn("error", await skill_create_tool("another", proposal.skill_md))
            with self.assertRaises(SkillMutationError):
                turn.propose(self.call("another"))
        import asyncio

        self.assertIn(
            "error",
            await asyncio.create_task(
                skill_create_tool(proposal.name, proposal.skill_md), context=retained
            ),
        )
        with database_connection(self.home) as connection:
            records = read_skill_repository(connection, self.home).history()
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].operation_id, receipt["operation_id"])

    async def test_generic_yes_refuses_without_skill_or_ledger(self):
        from kairos_tools.skill_create import skill_create_tool, skill_creation_scope

        turn = self.turn()
        with skill_creation_scope(turn.capability):
            proposal, future = turn.propose(self.call())
            turn.decide(proposal.id, "session", proposal.sha256, "sim")
            self.assertEqual(await future, "deny")
            self.assertIn("error", await skill_create_tool(proposal.name, proposal.skill_md))
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_binding_replay_cancel_and_unapproved_direct_call(self):
        from kairos_skills.mutation_contract import SkillMutationError
        from kairos_tools.skill_create import skill_create_tool, skill_creation_scope

        turn = self.turn()
        with skill_creation_scope(turn.capability):
            proposal, future = turn.propose(self.call())
            for session, digest in [("other", proposal.sha256), ("session", "0" * 64)]:
                with self.assertRaises(SkillMutationError):
                    turn.decide(proposal.id, session, digest, "criar revisar-docs")
            self.assertIn("error", await skill_create_tool(proposal.name, proposal.skill_md))
            self.assertFalse(future.done())
            turn.close()
            self.assertTrue(future.cancelled())
            with self.assertRaises(SkillMutationError):
                turn.decide(proposal.id, "session", proposal.sha256, "criar revisar-docs")
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_invalid_arguments_are_correctable_and_have_no_effect(self):
        import json

        from skill_mutation_fixtures import creation_text

        from kairos_skills.mutation_contract import SkillMutationError

        turn = self.turn()
        for raw in [
            '{"name":"a","name":"b","skill_md":"x"}',
            "{}",
            "[]",
            '{"name":false,"skill_md":"x"}',
            json.dumps({"name": "different", "skill_md": creation_text()}),
            json.dumps({"name": "revisar-docs", "skill_md": creation_text(body="x" * 16384)}),
            "\ud800",
        ]:
            with self.subTest(raw=raw[:30]), self.assertRaises(SkillMutationError):
                turn.propose(self.call(raw=raw))
        proposal, _ = turn.propose(self.call())
        self.assertEqual(proposal.name, "revisar-docs")
        turn.close()
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_native_dispatch_and_normal_chat_do_not_grant_creation(self):
        from kairos_integration.chat_tools import (
            chat_tool_definitions,
            execute_chat_tool,
            needs_tool_approval,
        )
        from kairos_tools import ToolRegistry, register_builtin_tools
        from kairos_tools.skill_create import skill_creation_scope

        registry = ToolRegistry()
        register_builtin_tools(registry)
        turn = self.turn()
        with skill_creation_scope(turn.capability):
            self.assertNotIn(
                "skill_create",
                [
                    d["function"]["name"]
                    for d in chat_tool_definitions(definitions=registry.get_definitions())
                ],
            )
        result = await execute_chat_tool(self.call())
        self.assertTrue(result.is_error)
        self.assertTrue(needs_tool_approval("skill_create"))
        self.assertEqual(list(self.home.iterdir()), [])

    async def test_registry_override_never_runs_in_active_scope(self):
        from kairos_tools import ToolRegistry
        from kairos_tools.skill_create import register_skill_create_tools, skill_creation_scope

        registry = ToolRegistry()
        register_skill_create_tools(registry)
        marker = self.home / "override"

        def override(**_):
            marker.write_text("unsafe")
            return {"state": "committed"}

        registry.register("skill_create", override, {})
        with skill_creation_scope(self.turn().capability):
            result = registry.dispatch("skill_create", {"name": "x", "skill_md": "x"})
        self.assertIn("error", result)
        self.assertFalse(marker.exists())

    async def test_total_json_utf8_boundary_and_escaped_body(self):
        import json

        from skill_mutation_fixtures import creation_text

        from kairos_skills.mutation_contract import SkillMutationError

        base = json.dumps(
            {"name": "revisar-docs", "skill_md": creation_text(body="á")}, ensure_ascii=False
        )
        raw = base + " " * (16384 - len(base.encode()))
        turn = self.turn()
        proposal, _ = turn.propose(self.call(raw=raw))
        self.assertIn("á", proposal.skill_md)
        turn.close()
        with self.assertRaises(SkillMutationError):
            self.turn().propose(self.call(raw=raw + " "))
        escaped = json.dumps({"name": "revisar-docs", "skill_md": creation_text(body="á" * 3000)})
        with self.assertRaises(SkillMutationError):
            self.turn().propose(self.call(raw=escaped))
