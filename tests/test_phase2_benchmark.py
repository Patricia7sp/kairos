from dataclasses import replace

import pytest

from scripts.e03.measurements import ControlSample, summarize_samples


def sample(**changes):
    base = ControlSample(
        scenario="ocioso",
        outcome="denied",
        session_id="e03-synthetic-" + "a" * 32,
        approval_id="b" * 32,
        call_id="e03-" + "c" * 32,
        send_ms=10.0,
        ack_http_ms=12.0,
        persist_ms=15.0,
        stream_ms=11.0,
        dom_ms=14.0,
        persisted_message_id=1,
        persisted_call_id="e03-" + "c" * 32,
        persisted_status="denied",
    )
    return replace(base, **changes)


def test_compound_ack_waits_for_persistence_and_r_is_explicit_bound():
    result = summarize_samples([sample()])
    row = result["samples"][0]
    assert row["controle_local_ms"] == 5.0
    assert row["stream_dom_ms"] == 3.0
    assert row["evento_painel_interval_ms"] == [3.0, 4.0]
    assert row["evento_painel_upper_ms"] == 4.0
    assert row["includes_network"] is True
    assert row["render_measurement"] == "visible_dom_two_frames"
    assert result["scenarios"]["ocioso"]["complete"] is False


def test_nearest_rank_uses_the_full_valid_population():
    rows = [
        sample(
            send_ms=0,
            ack_http_ms=i + 0.5,
            persist_ms=i + 1.5,
            stream_ms=i + 1,
            dom_ms=i + 2,
            persisted_message_id=i + 1,
            approval_id=f"{i:032x}",
            call_id="e03-" + f"{i:032x}",
            persisted_call_id="e03-" + f"{i:032x}",
        )
        for i in range(30)
    ]
    result = summarize_samples(rows)
    scenario = result["scenarios"]["ocioso"]
    assert scenario["attempts"] == 30
    assert scenario["clean_denies"] == 30
    assert scenario["controle_local_p95_ms"] == 29.5
    assert scenario["controle_local_max_ms"] == 30.5
    assert scenario["evento_painel_upper_p95_ms"] == 30
    assert scenario["complete"] is True
    assert result["gate"] == "inconclusive"  # outros cenários não foram executados


@pytest.mark.parametrize("outcome", ["timeout", "erro"])
def test_failures_remain_in_denominator_and_disable_percentiles(outcome):
    result = summarize_samples(
        [
            sample(
                approval_id=f"{i:032x}",
                call_id="e03-" + f"{i:032x}",
                persisted_call_id="e03-" + f"{i:032x}",
                persisted_message_id=i + 1,
            )
            for i in range(30)
        ]
        + [sample(outcome=outcome, persisted_message_id=31)]
    )
    scenario = result["scenarios"]["ocioso"]
    assert scenario["attempts"] == 31
    assert scenario["counts"][outcome] == 1
    assert scenario["clean_denies"] == 30
    assert scenario["controle_local_p95_ms"] is None
    assert scenario["complete"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"persisted_message_id": None},
        {"persisted_call_id": "e03-" + "d" * 32},
        {"ack_http_ms": None},
        {"dom_ms": 9},
        {"stream_ms": 8},
        {"send_ms": float("nan")},
        {"dom_visible": False},
    ],
)
def test_invalid_proof_or_clock_cannot_become_clean_deny(changes):
    result = summarize_samples([sample(**changes)])
    scenario = result["scenarios"]["ocioso"]
    assert scenario["attempts"] == 1
    assert scenario["clean_denies"] == 0
    assert scenario["counts"]["erro"] == 1
    assert result["samples"][0]["validation_errors"]


def test_opaque_input_is_not_accepted_as_export_metadata():
    with pytest.raises(ValueError, match="amostra inválida"):
        summarize_samples([sample(session_id="private credential text")])


def test_duplicate_proof_cannot_satisfy_minimum_clean_denies():
    result = summarize_samples([sample() for _ in range(30)])
    scenario = result["scenarios"]["ocioso"]
    assert scenario["attempts"] == 30
    assert scenario["clean_denies"] == 1
    assert scenario["counts"]["erro"] == 29
    assert scenario["controle_local_p95_ms"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"persisted_status": "private-token"},
        {"ack_http_ms": "private-token"},
        {"persisted_call_id": "private-token"},
    ],
)
def test_untrusted_fields_are_rejected_before_export(changes):
    with pytest.raises(ValueError, match="amostra inválida"):
        summarize_samples([sample(outcome="erro", **changes)])


def test_failed_attempt_before_approval_keeps_missing_correlation_explicit():
    result = summarize_samples(
        [
            sample(
                outcome="timeout",
                approval_id=None,
                call_id=None,
                persisted_message_id=None,
                persisted_call_id=None,
                persisted_status=None,
            )
        ]
    )
    assert result["samples"][0]["approval_id"] is None
    assert result["scenarios"]["ocioso"]["counts"]["timeout"] == 1
    assert result["gate"] == "inconclusive"
