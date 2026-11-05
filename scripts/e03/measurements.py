"""Resumo estrito de amostras do navegador; falha não vira percentil numérico."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass

SCENARIOS = ("ocioso", "conversa_painel", "auditoria", "execucao_isolada", "recuperacao")


@dataclass(frozen=True)
class ControlSample:
    scenario: str
    outcome: str
    session_id: str
    approval_id: str | None = None
    call_id: str | None = None
    send_ms: float | None = None
    ack_http_ms: float | None = None
    persist_ms: float | None = None
    stream_ms: float | None = None
    dom_ms: float | None = None
    persisted_message_id: int | None = None
    persisted_call_id: str | None = None
    persisted_status: str | None = None
    dom_visible: bool = True
    dom_mutation_ms: float | None = None
    http_status: int | None = None
    ack_decided: bool = False
    error_kind: str | None = None


def _number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _validate_shape(sample):
    if not isinstance(sample, ControlSample):
        raise ValueError("amostra inválida")
    if not isinstance(sample.scenario, str) or sample.scenario not in SCENARIOS:
        raise ValueError("amostra inválida")
    if not isinstance(sample.outcome, str) or sample.outcome not in {"denied", "timeout", "erro"}:
        raise ValueError("amostra inválida")
    for value, pattern, optional in (
        (sample.session_id, r"e03-synthetic-[a-f0-9]{32}", False),
        (sample.approval_id, r"[a-f0-9]{32}", sample.outcome != "denied"),
        (sample.call_id, r"e03-[a-f0-9]{32}", sample.outcome != "denied"),
        (sample.persisted_call_id, r"e03-[a-f0-9]{32}", True),
    ):
        if value is None and optional:
            continue
        if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
            raise ValueError("amostra inválida")
    if sample.persisted_status not in (None, "denied") or type(sample.dom_visible) is not bool:
        raise ValueError("amostra inválida")
    if any(
        value is not None and type(value) not in (int, float)
        for value in (
            sample.send_ms,
            sample.ack_http_ms,
            sample.persist_ms,
            sample.stream_ms,
            sample.dom_ms,
            sample.dom_mutation_ms,
        )
    ):
        raise ValueError("amostra inválida")
    _validate_protocol(sample)
    if sample.persisted_message_id is not None and type(sample.persisted_message_id) is not int:
        raise ValueError("amostra inválida")


def _validate_protocol(sample):
    if type(sample.ack_decided) is not bool:
        raise ValueError("amostra inválida")
    if sample.http_status is not None and (
        type(sample.http_status) is not int or not 100 <= sample.http_status <= 599
    ):
        raise ValueError("amostra inválida")
    if sample.error_kind not in (
        None,
        "http",
        "transport",
        "correlation",
        "deadline",
        "persist",
        "dom",
        "clock",
        "cancelled",
        "workload",
    ):
        raise ValueError("amostra inválida")


def _row(sample):
    _validate_shape(sample)
    row = asdict(sample)
    errors = []
    if sample.outcome == "denied":
        times = [
            sample.send_ms,
            sample.ack_http_ms,
            sample.persist_ms,
            sample.stream_ms,
            sample.dom_ms,
            sample.dom_mutation_ms,
        ]
        if not all(_number(value) for value in times):
            errors.append("missing_or_invalid_clock")
        elif not (
            sample.send_ms <= sample.ack_http_ms
            and sample.send_ms <= sample.persist_ms
            and sample.send_ms <= sample.stream_ms <= sample.dom_mutation_ms <= sample.dom_ms
        ):
            errors.append("incompatible_clock_order")
        if (
            type(sample.persisted_message_id) is not int
            or sample.persisted_message_id <= 0
            or sample.persisted_call_id != sample.call_id
            or sample.persisted_status != "denied"
        ):
            errors.append("persistence_not_confirmed")
        if sample.http_status != 200 or sample.ack_decided is not True:
            errors.append("http_ack_not_confirmed")
        if sample.dom_visible is not True:
            errors.append("dom_not_visible")
    if errors:
        row["outcome"] = "erro"
    valid = row["outcome"] == "denied" and not errors
    for key, value in list(row.items()):
        if isinstance(value, float) and not math.isfinite(value):
            row[key] = None
    row.update(
        {
            "validation_errors": errors,
            "controle_local_ms": max(sample.ack_http_ms, sample.persist_ms) - sample.send_ms
            if valid
            else None,
            "stream_dom_ms": sample.dom_ms - sample.stream_ms if valid else None,
            "evento_painel_interval_ms": [
                sample.dom_ms - sample.stream_ms,
                sample.dom_ms - sample.send_ms,
            ]
            if valid
            else None,
            "evento_painel_upper_ms": sample.dom_ms - sample.send_ms if valid else None,
            "includes_network": True,
            "control_path": "browser_to_linux_composite_persisted_ack",
            "render_measurement": "visible_dom_two_frames",
            "event_measurement": "causal_upper_bound_not_exact_commit_time",
        }
    )
    return row


def _p95(values):
    return sorted(values)[math.ceil(0.95 * len(values)) - 1]


def summarize_samples(samples, minimum=30):
    if type(minimum) is not int or minimum < 30:
        raise ValueError("mínimo do smoke não pode ser menor que 30")
    rows = []
    seen = set()
    for sample in samples:
        row = _row(sample)
        identities = {
            ("approval", row["approval_id"]),
            ("call", row["session_id"], row["call_id"]),
            ("receipt", row["session_id"], row["persisted_message_id"]),
        }
        identities = {identity for identity in identities if identity[-1] is not None}
        if seen & identities:
            row["outcome"] = "erro"
            row["validation_errors"].append("duplicate_correlation")
            for key in (
                "controle_local_ms",
                "stream_dom_ms",
                "evento_painel_interval_ms",
                "evento_painel_upper_ms",
            ):
                row[key] = None
        seen.update(identities)
        rows.append(row)
    scenarios = {}
    for name in SCENARIOS:
        selected = [row for row in rows if row["scenario"] == name]
        counts = Counter(row["outcome"] for row in selected)
        complete = len(selected) >= minimum and counts["denied"] == len(selected)
        controls = [row["controle_local_ms"] for row in selected] if complete else []
        renders = [row["evento_painel_upper_ms"] for row in selected] if complete else []
        scenarios[name] = {
            "attempts": len(selected),
            "clean_denies": counts["denied"],
            "counts": {key: counts[key] for key in ("denied", "timeout", "erro")},
            "complete": complete,
            "controle_local_p95_ms": _p95(controls) if complete else None,
            "controle_local_max_ms": max(controls) if complete else None,
            "evento_painel_upper_p95_ms": _p95(renders) if complete else None,
            "evento_painel_upper_max_ms": max(renders) if complete else None,
        }
    complete = all(value["complete"] for value in scenarios.values())
    passed = complete and all(
        value["controle_local_p95_ms"] <= 2000 and value["evento_painel_upper_p95_ms"] <= 3000
        for value in scenarios.values()
    )
    return {
        "schema_version": 1,
        "samples": rows,
        "scenarios": scenarios,
        "gate": "passed" if passed else "failed" if complete else "inconclusive",
        "scope": "synthetic_deny_only_smoke_not_sla",
        "gate_scope": "measurements_only",
        "minimum_clean_denies": minimum,
    }
