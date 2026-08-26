from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kairos_integration import InteractionEnvelope
from kairos_integration.selection_context import SelectionContextLoader
from kairos_providers import ProviderModelRef, SelectionReason
from kairos_state import connect, initialize_schema
from kairos_state.repositories import SessionRepository


def ref(model: str, provider: str = "p") -> ProviderModelRef:
    return ProviderModelRef(provider, model)


def envelope(**overrides: object) -> InteractionEnvelope:
    payload = {
        "conversation_id": "s1",
        "source": "web",
        "content": "olá",
        "profile": "work",
    }
    payload.update(overrides)
    return InteractionEnvelope(**payload)


class SelectionContextLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.db"
        self.db = connect(self.path)
        initialize_schema(self.db)
        self.sessions = SessionRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def loader(
        self,
        *,
        profile: ProviderModelRef | None = None,
        activity: ProviderModelRef | None = None,
        global_default: ProviderModelRef | None = None,
        profile_configs: dict[str, object] | None = None,
        global_config: dict[str, object] | None = None,
    ) -> SelectionContextLoader:
        built_profile_configs = profile_configs or {}
        if profile is not None or activity is not None:
            profile_config: dict[str, object] = dict(
                built_profile_configs.get("work", {})
                if isinstance(built_profile_configs.get("work"), dict)
                else {}
            )
            if profile is not None:
                profile_config["model"] = {
                    "provider": profile.provider,
                    "model": profile.model,
                }
            if activity is not None:
                profile_config["auxiliary_models"] = {
                    "vision": f"{activity.provider}/{activity.model}"
                }
            built_profile_configs["work"] = profile_config
        built_global_config = global_config or {}
        if global_default is not None:
            built_global_config["model"] = {
                "provider": global_default.provider,
                "model": global_default.model,
            }
        return SelectionContextLoader(
            self.sessions,
            profile_configs=built_profile_configs,
            global_config=built_global_config,
        )

    def test_contexto_monta_cinco_camadas_sem_escolher_fallback(self) -> None:
        self.sessions.create("s1", source="web")
        self.sessions.set_selection(
            "s1",
            ref("c"),
            {},
            reason=SelectionReason.CONVERSATION_OVERRIDE,
        )

        context = self.loader(
            profile=ref("p"),
            activity=ref("a"),
            global_default=ref("g"),
        ).load(envelope(override=ref("m"), activity="vision"))

        self.assertEqual(context.message, ref("m"))
        self.assertEqual(context.conversation, ref("c"))
        self.assertEqual(context.activity, ref("a"))
        self.assertEqual(context.profile, ref("p"))
        self.assertEqual(context.global_default, ref("g"))

    def test_sem_configuracao_nao_inventa_anthropic(self) -> None:
        context = self.loader().load(envelope(profile=None))

        self.assertTrue(all(value is None for value in vars(context).values()))

    def test_modelo_global_com_provider_irmao_vira_ref(self) -> None:
        context = self.loader(
            global_config={
                "provider": "gemini",
                "model": "gemini-2.0-flash",
            }
        ).load(envelope(profile=None))

        self.assertEqual(context.global_default, ProviderModelRef("gemini", "gemini-2.0-flash"))
