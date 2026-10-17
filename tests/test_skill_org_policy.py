"""Política local recusa fontes instáveis e não confunde homes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from kairos_skills.mutation_contract import SkillMutationError
from kairos_skills.org_policy import observe_org_policy, org_policy_context, parse_org_policy
from kairos_skills.org_policy_contract import OrgPolicyContext


class OrgPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.source = self.root / "private-policy.json"
        self.document = {
            "version": 1,
            "bindings": [
                {
                    "home": str(self.home),
                    "name": "local-skill",
                    "organization_id": "equipe-exemplo",
                    "active": False,
                }
            ],
        }
        self.write()

    def write(self):
        self.source.write_text(json.dumps(self.document), encoding="utf-8")

    def context(self):
        return org_policy_context({"KAIROS_SKILL_ORG_POLICY": str(self.source)})

    def assert_kind(self, expected, operation):
        with self.assertRaises(SkillMutationError) as caught:
            operation()
        self.assertEqual(caught.exception.kind, expected)
        self.assertNotIn(str(self.source), str(caught.exception))

    def observe(self, home=None):
        with observe_org_policy(self.context(), self.home if home is None else home):
            pass

    def test_context_requires_explicit_valid_selection(self):
        none = org_policy_context({})
        self.assertEqual(none.mode, "none")
        self.assertIsNone(none.source)
        with self.assertRaises(FrozenInstanceError):
            none.mode = "file"
        for path in ("", "relative.json", " /tmp/policy.json"):
            with self.subTest(path=path):
                self.assert_kind(
                    "input", lambda path=path: org_policy_context({"KAIROS_SKILL_ORG_POLICY": path})
                )
        for mode, source in (("file", None), ("none", self.source), ("other", None)):
            with self.subTest(mode=mode):
                self.assert_kind(
                    "input", lambda mode=mode, source=source: OrgPolicyContext(mode, source)
                )

    def test_receipt_filters_home_and_does_not_discard_inactive_binding(self):
        other = self.root / "other"
        self.document["bindings"].append(
            dict(self.document["bindings"][0], home=str(other), organization_id="outro")
        )
        self.write()
        with observe_org_policy(self.context(), self.home) as session:
            receipt = session.receipt("local-skill")
            self.assertEqual(
                [(binding.organization_id, binding.active) for binding in receipt.bindings],
                [("equipe-exemplo", False)],
            )
            self.assertEqual(session.receipt("another-skill").bindings, ())
            self.assertEqual(len(receipt.source_digest), 64)
            self.assertEqual(len(receipt.observation_id), 32)
            self.assertNotIn(str(self.home), repr(receipt))
            self.assertNotIn(str(self.source), repr(session))
        self.assert_kind("conflict", session.revalidate)
        self.assertFalse(other.exists())

    def test_none_observation_is_explicit_and_has_no_source_digest(self):
        with observe_org_policy(org_policy_context({}), self.home) as session:
            receipt = session.receipt("local-skill")
            self.assertIsNone(receipt.source_digest)
            self.assertEqual(receipt.bindings, ())

    def test_parser_rejects_ambiguous_or_wrong_types(self):
        invalid = [
            b'{"version":true,"bindings":[]}',
            b'{"version":1,"version":1,"bindings":[]}',
            b'{"version":1,"bindings":[],"other":0}',
            b'{"version":0,"bindings":[]}',
            b'{"version":2,"bindings":[]}',
            b'{"version":1,"bindings":{}}',
            b"[]",
            b"null",
            b"\xff",
            b"{",
            b'{"version":1,"bindings":[],"x":NaN}',
            b"[" * 2000,
        ]
        binding = self.document["bindings"][0]
        for key, values in {
            "organization_id": ("", "a" * 65, "Área", True, "-team"),
            "active": (None, 1, "false"),
            "name": ("bad/name", "_org", "", 12),
            "home": ("relative", str(self.home) + "/../escape", str(self.home) + "/."),
        }.items():
            invalid.extend(
                json.dumps({"version": 1, "bindings": [dict(binding, **{key: value})]}).encode()
                for value in values
            )
        invalid.append(json.dumps({"version": 1, "bindings": [binding, binding]}).encode())
        missing = dict(binding)
        del missing["active"]
        invalid.append(json.dumps({"version": 1, "bindings": [missing]}).encode())
        invalid.append(
            (
                '{"version":1,"bindings":[{"home":'
                + json.dumps(str(self.home))
                + ',"name":"local-skill","organization_id":"a","active":true,"active":false}]}'
            ).encode()
        )
        for raw in invalid:
            with self.subTest(raw=raw[:100]):
                self.assert_kind("input", lambda raw=raw: parse_org_policy(raw))

    def test_absent_or_unreadable_source_fails_closed(self):
        self.source.unlink()
        self.assert_kind("unavailable", self.observe)
        self.write()
        original = os.open

        def denied(path, *args, **kwargs):
            if path == self.source.name:
                raise PermissionError("private backend sentinel")
            return original(path, *args, **kwargs)

        with patch("os.open", side_effect=denied):
            self.assert_kind("unavailable", self.observe)

    def test_source_links_and_oversize_are_rejected(self):
        original = self.root / "original.json"
        self.source.rename(original)
        self.source.symlink_to(original)
        self.assert_kind("input", self.observe)
        self.source.unlink()
        os.link(original, self.source)
        self.assert_kind("input", self.observe)
        self.source.unlink()
        self.source.write_bytes(b" " * (1024 * 1024 + 1))
        self.assert_kind("input", self.observe)

    def test_fifo_source_never_blocks(self):
        self.source.unlink()
        os.mkfifo(self.source)
        code = """
import sys
from pathlib import Path
from kairos_skills.org_policy import observe_org_policy, org_policy_context
from kairos_skills.mutation_contract import SkillMutationError
try:
    with observe_org_policy(org_policy_context({'KAIROS_SKILL_ORG_POLICY':sys.argv[1]}),Path(sys.argv[2])):
        raise AssertionError('FIFO accepted')
except SkillMutationError as error:
    assert error.kind == 'input', error.kind
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.source), str(self.home)],
            capture_output=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_bytes_new_inode_and_content_changes_are_conflicts(self):
        for replacement in (True, False):
            with self.subTest(replacement=replacement):
                self.write()
                with observe_org_policy(self.context(), self.home) as session:
                    if replacement:
                        other = self.root / "new.json"
                        other.write_bytes(self.source.read_bytes())
                        other.replace(self.source)
                    else:
                        self.source.write_bytes(b"{}")
                    self.assert_kind("conflict", session.revalidate)

    def test_source_becoming_empty_oversize_or_hardlinked_is_conflict(self):
        for change in ("empty", "oversize", "hardlink"):
            with self.subTest(change=change):
                self.write()
                with observe_org_policy(self.context(), self.home) as session:
                    if change == "hardlink":
                        os.link(self.source, self.root / "new-link")
                    else:
                        self.source.write_bytes(
                            b"" if change == "empty" else b" " * (1024 * 1024 + 1)
                        )
                    self.assert_kind("conflict", session.revalidate)
                (self.root / "new-link").unlink(missing_ok=True)

    def test_replaced_policy_ancestor_and_home_are_detected(self):
        directory = self.root / "policies"
        directory.mkdir()
        self.source = directory / "policy.json"
        self.write()
        with observe_org_policy(self.context(), self.home) as session:
            directory.rename(self.root / "retained")
            directory.mkdir()
            self.write()
            self.assert_kind("conflict", session.revalidate)
        with observe_org_policy(self.context(), self.home) as session:
            self.home.rename(self.root / "retained-home")
            self.home.mkdir()
            self.assert_kind("conflict", session.revalidate)

    def test_home_symlink_is_rejected_and_absent_home_not_created(self):
        alias = self.root / "alias"
        alias.symlink_to(self.home, target_is_directory=True)
        self.assert_kind("input", lambda: self.observe(alias))
        self.assert_kind("input", lambda: self.observe(alias / "absent"))
        absent = self.root / "absent"
        with observe_org_policy(self.context(), absent) as session:
            self.assertEqual(session.receipt("local-skill").bindings, ())
        self.assertFalse(absent.exists())
        self.document["bindings"][0]["home"] = str(alias / "missing")
        self.write()
        self.assert_kind("input", self.observe)

    def test_special_source_error_log_is_sanitized(self):
        self.source.write_bytes(b"secret-invalid-json-sentinel")
        with self.assertLogs("kairos_skills.org_policy", level="WARNING") as logs:
            self.assert_kind("input", self.observe)
        output = " ".join(logs.output)
        self.assertNotIn(str(self.source), output)
        self.assertNotIn("secret-invalid-json-sentinel", output)
        self.assertEqual(logs.records[0].kind, "input")
