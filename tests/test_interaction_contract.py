from dataclasses import FrozenInstanceError

import pytest

from kairos_integration import (
    InteractionEnvelope,
    InteractionEvent,
    InteractionEventKind,
    InteractionSelectionSnapshot,
    InteractionToolResult,
)
from kairos_providers import (
    ProviderError,
    ProviderErrorKind,
    ProviderEvent,
    ProviderModelRef,
    SelectionReason,
    TokenUsage,
)


def test_snapshot_e_imutavel():
    snap = InteractionSelectionSnapshot(
        ref=ProviderModelRef("openai", "gpt"),
        reason=SelectionReason.MESSAGE_OVERRIDE,
        parameters={},
    )
    with pytest.raises(FrozenInstanceError):
        snap.ref = ProviderModelRef("openai", "other")


def test_snapshot_congela_parametros_aninhados():
    snap = InteractionSelectionSnapshot(
        ref=ProviderModelRef("openai", "gpt"),
        reason=SelectionReason.MESSAGE_OVERRIDE,
        parameters={"nested": {"items": ["one"]}},
    )

    with pytest.raises(TypeError):
        snap.parameters["nested"]["enabled"] = True
    with pytest.raises(AttributeError):
        snap.parameters["nested"]["items"].append("two")


def test_snapshot_exige_motivo():
    with pytest.raises(TypeError):
        InteractionSelectionSnapshot(ref=ProviderModelRef("openai", "gpt"))


def test_envelope_exige_conversa_e_conteudo():
    with pytest.raises(ValueError):
        InteractionEnvelope(conversation_id="", source="web", content="")


def test_provider_finish_nao_vira_evento_duplicado():
    assert InteractionEvent.from_provider(
        ProviderEvent(kind="finish", finish_reason="stop")
    ) is None


def test_provider_evento_e_traduzido_para_delta_e_uso():
    delta = InteractionEvent.from_provider(ProviderEvent(kind="text_delta", text="olá"))
    usage = InteractionEvent.from_provider(
        ProviderEvent(kind="usage", usage=TokenUsage(input_tokens=2, output_tokens=3))
    )

    assert delta is not None
    assert delta.kind is InteractionEventKind.DELTA
    assert delta.text == "olá"
    assert usage is not None
    assert usage.kind is InteractionEventKind.USAGE
    assert usage.usage == TokenUsage(input_tokens=2, output_tokens=3)


def test_turn_error_nao_expoe_payload_upstream():
    error = ProviderError.from_upstream(
        ProviderErrorKind.AUTH,
        {"body": "api_key=super-secret"},
        retryable=False,
    )

    event = InteractionEvent.turn_error(error)

    assert event.kind is InteractionEventKind.TURN_ERROR
    assert event.error == "credencial inválida ou ausente"
    assert "super-secret" not in repr(event)
    assert event.error_kind == "auth"
    assert event.retryable is False


def test_tool_result_tem_payload_tipado_e_sanitizado():
    result = InteractionToolResult(tool_call_id="call-1", content="resultado", is_error=False)
    event = InteractionEvent.from_tool_result(result)

    assert event.kind is InteractionEventKind.TOOL_RESULT
    assert event.tool_result == result


def test_tool_result_rejeita_payload_arbitrario():
    with pytest.raises(TypeError):
        InteractionEvent.from_tool_result({"tool_call_id": "call-1", "content": "unsafe"})
