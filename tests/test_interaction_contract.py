from dataclasses import FrozenInstanceError

import pytest

from kairos_integration import InteractionEnvelope, InteractionSelectionSnapshot
from kairos_providers import ProviderModelRef


def test_snapshot_e_imutavel():
    snap = InteractionSelectionSnapshot(ref=ProviderModelRef("openai", "gpt"), parameters={})
    with pytest.raises(FrozenInstanceError):
        snap.ref = ProviderModelRef("openai", "other")


def test_envelope_exige_conversa_e_conteudo():
    with pytest.raises(ValueError):
        InteractionEnvelope(conversation_id="", source="web", content="")
