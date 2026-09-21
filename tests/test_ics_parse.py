"""Parser/escritor `.ics` (kairos_tools.ics) — comportamento real, sem ler fonte."""

from datetime import UTC, datetime

import pytest

from kairos_tools.ics import (
    IcsParseError,
    add_event_to_text,
    format_event_block,
    parse_ics,
    remove_event_by_uid,
)


def _vcalendar(vevents: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + vevents + "END:VCALENDAR\r\n"


def _vevent(**lines: str) -> str:
    body = "".join(f"{key}:{value}\r\n" for key, value in lines.items())
    return "BEGIN:VEVENT\r\n" + body + "END:VEVENT\r\n"


def test_le_evento_basico_com_dtstart_dtend_utc():
    text = _vcalendar(
        _vevent(UID="u1", DTSTART="20260921T100000Z", DTEND="20260921T110000Z", SUMMARY="Reunião")
    )
    [event] = parse_ics(text)
    assert event.uid == "u1"
    assert event.summary == "Reunião"
    assert event.start == datetime(2026, 9, 21, 10, 0, tzinfo=UTC)
    assert event.end == datetime(2026, 9, 21, 11, 0, tzinfo=UTC)
    assert event.recurrence is None


def test_dobramento_de_linha_e_recomposto():
    summary = "x" * 90
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260921T100000Z\r\n"
        f"SUMMARY:{summary[:74]}\r\n {summary[74:]}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    [event] = parse_ics(text)
    assert event.summary == summary


def test_escapes_rfc_5545_sao_revertidos():
    [event] = parse_ics(
        _vcalendar(_vevent(UID="u1", DTSTART="20260921T100000Z", SUMMARY="a, b; c \\ d"))
    )
    assert event.summary == "a, b; c \\ d"


def test_descricao_quoted_printable_e_decodificada():
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260921T100000Z\r\n"
        "DESCRIPTION;ENCODING=QUOTED-PRINTABLE:caf=C3=A9 =C3=A9 =C3=A9\r\n"
        "SUMMARY:resumo\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    [event] = parse_ics(text)
    assert event.description == "café é é"


def test_tzid_resolvido_pelo_zoneinfo():
    [event] = parse_ics(
        _vcalendar(
            _vevent(UID="u1", DTSTART="20260921T100000", SUMMARY="x").replace(
                "DTSTART:", "DTSTART;TZID=America/Sao_Paulo:"
            )
        )
    )
    assert event.start.tzinfo is not None
    assert event.start.astimezone(UTC).hour == 13


def test_tzid_nao_resolvido_marca_limite_sem_mentir():
    [event] = parse_ics(
        _vcalendar(
            _vevent(UID="u1", DTSTART="20260921T100000", SUMMARY="x").replace(
                "DTSTART:", "DTSTART;TZID=America/Inexistente:"
            )
        )
    )
    assert event.start.tzinfo is None
    assert event.flag("tzid") == "America/Inexistente"
    assert event.flag("fuso") == "local-assumido"


def test_dtstart_so_data_e_dia_inteiro():
    [event] = parse_ics(
        _vcalendar(
            _vevent(UID="u1", DTSTART="20260921", SUMMARY="Dia todo").replace(
                "DTSTART:", "DTSTART;VALUE=DATE:"
            )
        )
    )
    assert event.start == datetime(2026, 9, 21)
    assert event.start.tzinfo is None


def test_duracao_vira_fim():
    [event] = parse_ics(
        _vcalendar(_vevent(UID="u1", DTSTART="20260921T100000Z", DURATION="PT1H30M", SUMMARY="x"))
    )
    assert event.end == datetime(2026, 9, 21, 11, 30, tzinfo=UTC)


def test_rrule_e_marcado_mas_nao_expandido():
    [event] = parse_ics(
        _vcalendar(
            _vevent(
                UID="u1", DTSTART="20260921T100000Z", RRULE="FREQ=DAILY;INTERVAL=2", SUMMARY="x"
            )
        )
    )
    assert event.recurrence == "FREQ=DAILY;INTERVAL=2"
    assert event.start == datetime(2026, 9, 21, 10, 0, tzinfo=UTC)


def test_vevent_sem_end_fecha_arquivo():
    text = "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260921T100000Z"
    with pytest.raises(IcsParseError, match="sem END:VEVENT"):
        parse_ics(text)


def test_vevent_sem_dtstart_fecha_bloco():
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nSUMMARY:x\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    with pytest.raises(IcsParseError, match="sem DTSTART"):
        parse_ics(text)


def test_linha_sem_dois_pontos_e_erro():
    text = _vevent(UID="u1", DTSTART="20260921T100000Z", SUMMARY="resumo").replace(
        "SUMMARY:", "SUMMARY GARBAGE", 1
    )
    with pytest.raises(IcsParseError, match="sem ':'"):
        parse_ics(text)


def test_add_propriedade_espelha_formato_canonico():
    block = format_event_block(
        uid="novo",
        summary="Foco profundo, ; ponto; \\",
        start=datetime(2026, 9, 21, 13, 0, tzinfo=UTC),
        end=datetime(2026, 9, 21, 14, 0, tzinfo=UTC),
    )
    assert "DTSTART:20260921T130000Z" in block
    assert "DTEND:20260921T140000Z" in block
    [event] = parse_ics(_vcalendar(block))
    assert event.summary == "Foco profundo, ; ponto; \\"


def test_add_propriedade_flutuante_nao_FORCA_utc():
    block = format_event_block(
        uid="novo",
        summary="Local",
        start=datetime(2026, 9, 21, 13, 0),
        end=datetime(2026, 9, 21, 14, 0),
    )
    assert "DTSTART:20260921T130000" in block
    assert "T130000Z" not in block


def test_add_evento_cria_envelope_e_splice():
    block = format_event_block(
        uid="u1",
        summary="x",
        start=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        end=datetime(2026, 9, 21, 11, 0, tzinfo=UTC),
    )
    criado = add_event_to_text("", block)
    assert criado.startswith("BEGIN:VCALENDAR")
    assert "END:VCALENDAR" in criado
    assert len(parse_ics(criado)) == 1
    preenchido = add_event_to_text(
        _vcalendar(_vevent(UID="antigo", DTSTART="20260920T100000Z", SUMMARY="v")), block
    )
    assert len(parse_ics(preenchido)) == 2


def test_add_evento_sem_end_vcalendar_recusa():
    block = format_event_block(
        uid="u1",
        summary="x",
        start=datetime(2026, 9, 21, 10, 0, tzinfo=UTC),
        end=datetime(2026, 9, 21, 11, 0, tzinfo=UTC),
    )
    with pytest.raises(IcsParseError, match="sem END:VCALENDAR"):
        add_event_to_text("começo sem envelope", block)


def test_remove_por_uid_mantem_ordem_e_remove_somente_o_alvo():
    base = _vcalendar(
        _vevent(UID="a", DTSTART="20260920T100000Z", SUMMARY="um")
        + _vevent(UID="b", DTSTART="20260921T100000Z", SUMMARY="dois")
        + _vevent(UID="a", DTSTART="20260922T100000Z", SUMMARY="tres")
    )
    novo, removidos = remove_event_by_uid(base, "a")
    assert removidos == 2
    restantes = parse_ics(novo)
    assert [event.uid for event in restantes] == ["b"]


def test_remove_uid_ausente_nao_altera_nada():
    base = _vcalendar(_vevent(UID="a", DTSTART="20260920T100000Z", SUMMARY="um"))
    novo, removidos = remove_event_by_uid(base, "zz")
    assert removidos == 0
    assert novo == base
