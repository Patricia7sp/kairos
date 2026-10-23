"""CLI executável informa publicação pendente e código diferente de zero."""

import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

from test_skill_publication_writers import PublicationWriterFixture

from kairos_skills.publication_contract import ManifestDelta
from kairos_skills.publication_session import publication_session
from kairos_skills.publication_sources import observe_bundled_source
from kairos_skills.publications import SkillPublicationService
from kairos_skills.sync import MANIFEST_NAME, origin_hash, write_manifest


class CliPublicationTests(PublicationWriterFixture):
    def test_cli_partial_is_nonzero_and_has_publication_id(self):
        with (
            observe_bundled_source(self.home, policy=self.policy) as context,
            publication_session(self.home, policy=self.policy, context=context) as session,
        ):
            name = sorted(context._bundled)[0]
            source, _proof = context.resolve(name)
            digest = origin_hash(source)
            with patch(
                "kairos_state.repositories.skill_publications.SkillPublicationRepository.confirm_manifest",
                side_effect=OSError("after manifest fsync"),
            ):
                result = SkillPublicationService(session).publish(
                    name, manifest_delta=ManifestDelta(name, None, digest)
                )
            self.assertFalse(result.complete)
            other_names = set(context._bundled) - {name}
            for other in other_names:
                target = self.home / "skills" / other
                target.mkdir()
                (target / "SKILL.md").write_bytes(b"local override preserved")
            write_manifest(self.home / "skills" / MANIFEST_NAME, {name: "a" * 32})
        env = dict(os.environ, KAIROS_HOME=str(self.home))
        env.pop("KAIROS_SKILL_ORG_POLICY", None)
        process = subprocess.run(
            [str(Path(".venv/bin/kairos").absolute()), "sync", "now", "--json"],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        self.assertNotEqual(process.returncode, 0)
        payload = json.loads(process.stdout)
        self.assertTrue(payload["failed"])
        self.assertIn(result.operation_id, payload["pendentes"])
        self.assertEqual(payload["copiadas"], [])
        for other in other_names:
            self.assertEqual(
                (self.home / "skills" / other / "SKILL.md").read_bytes(),
                b"local override preserved",
            )
        self.assertNotIn(str(self.root), process.stdout + process.stderr)
