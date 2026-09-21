"""Ferramenta `calendar` — gate, leitura, mutação e aprovação por subcomando."""

from datetime import UTC, datetime

import pytest
import yaml

from kairos_integration.chat_tools import needs_tool_approval
from kairos_tools.calendar import _calendar_available, calendar_tool, register_calendar_tool
from kairos_tools.ics import parse_ics
from kairos_tools.registry import ToolRegistry

UID_A = "08df1a2e-d0a6-4d2a-8b89-d589600f5824"
UID_B = "78feb8c9-10af-4cdc-9d40-19d2b0c7e0b1"


def _vevent(uid: str, dtstart: str, summary: str, *, dtend: str | None = None) -> str:
    extra = f"\r\nDTEND:{dtend}" if dtend else ""
    return (
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTART:{dtstart}{extra}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
    )


def _vcalendar(*vevents: str) -> str:
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + "".join(vevents) + "END:VCALENDAR\r\n"


def _write_calendar(home, source: str) -> None:
    (home / "config.yaml").write_text(
        yaml.safe_dump({"calendar": {"source": source}}, sort_keys=False), encoding="utf-8"
    )


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    return tmp_path


def test_fonte_nao_configurada_omite_a_ferramenta(home):
    reg = ToolRegistry()
    register_calendar_tool(reg)
    assert reg.is_toolset_available("calendar") is False
    assert all(d["function"]["name"] != "calendar" for d in reg.get_definitions())


def test_fonte_inexistente_omite(home):
    _write_calendar(home, str(home / "sem-agenda.ics"))
    assert _calendar_available() is False


def test_fonte_configurada_e_presente_expoe_a_ferramenta(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(_vevent(UID_A, "20260921T100000Z", "Primeiro")), encoding="utf-8")
    _write_calendar(home, str(source))
    reg = ToolRegistry()
    register_calendar_tool(reg)
    assert reg.is_toolset_available("calendar") is True
    assert any(d["function"]["name"] == "calendar" for d in reg.get_definitions())


def test_subcomando_nao_suportado_recusa(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(subcommand="push")
    assert result["success"] is False
    assert result["error"] == "subcomando não suportado: push"


def test_sem_fonte_configurada_recusa_nomeada(home):
    result = calendar_tool(subcommand="today")
    assert result["success"] is False
    assert "calendar.source" in result["error"]


EVENTOS_FIXOS = _vevent(
    UID_A, "20260921T100000Z", "Reunião de alinhamento", dtend="20260921T110000Z"
) + _vevent(UID_B, "20260922T100000Z", "Consulta médica", dtend="20260922T110000Z")


def test_today_lista_somente_eventos_do_dia(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(EVENTOS_FIXOS), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(subcommand="today", now="2026-09-21T12:00:00Z", limite=50)
    assert result["success"] is True
    assert result["count"] == 1
    assert result["total_no_intervalo"] == 1
    [evento] = result["eventos"]
    assert evento["titulo"] == "Reunião de alinhamento"
    assert evento["fuso"] == "consciente"
    assert evento["inicio"] == "2026-09-21T10:00:00+00:00"


def test_range_eventos_ordenados_na_janela(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(EVENTOS_FIXOS), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-23T23:59:59Z",
        limite=50,
    )
    assert result["success"] is True
    assert [evento["uid"] for evento in result["eventos"]] == [UID_A, UID_B]


def test_range_desde_apos_ate_recusa(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(EVENTOS_FIXOS), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-22T00:00:00Z",
        ate="2026-09-21T00:00:00Z",
        limite=50,
    )
    assert result["success"] is False
    assert "anterior ou igual" in result["error"]


@pytest.mark.parametrize("limite", [0, 201, "cinco", None])
def test_range_limite_invalido_recusa(home, limite):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(EVENTOS_FIXOS), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-23T00:00:00Z",
        limite=limite,
    )
    assert result["success"] is False


def test_leitura_em_diretorio_agrega_arquivos(home):
    pasta = home / "agenda-dir"
    pasta.mkdir()
    (pasta / "b.ics").write_text(
        _vcalendar(_vevent(UID_B, "20260922T100000Z", "B")), encoding="utf-8"
    )
    (pasta / "a.ics").write_text(
        _vcalendar(_vevent(UID_A, "20260921T100000Z", "A")), encoding="utf-8"
    )
    _write_calendar(home, str(pasta))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-23T00:00:00Z",
        limite=50,
    )
    assert result["success"] is True
    assert result["count"] == 2
    assert result["source"] == str(pasta)


def test_arquivo_corrompido_fecha_sem_parcial(home):
    source = home / "agenda.ics"
    source.write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:u1\r\nDTSTART:20260921T100000Z", encoding="utf-8"
    )
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-23T00:00:00Z",
        limite=50,
    )
    assert result["success"] is False
    assert result["arquivos_com_erro"]


def test_recorrencia_e_marcada_mas_nao_expandida(home):
    base = _vevent(UID_A, "20260921T100000Z", "Diário").replace(
        "END:VEVENT", "RRULE:FREQ=DAILY\r\nEND:VEVENT"
    )
    source = home / "agenda.ics"
    source.write_text(_vcalendar(base), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-28T00:00:00Z",
        ate="2026-09-30T00:00:00Z",
        limite=50,
    )
    # A recorrência existe, mas v1 não expande — a janela futura fica vazia de
    # forma honesta: nenhuma ocorrência inventada, nenhuma mentira.
    assert result["success"] is True
    assert result["count"] == 0
    base_window = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-22T00:00:00Z",
        limite=50,
    )
    [evento] = base_window["eventos"]
    assert evento["recorrencia"] == "rrule-nao-expandida"
    assert "não expandida" in evento["observacao"]


def test_add_grava_evento_no_arquivo(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="add",
        titulo="Foco profundo",
        inicio="2026-09-21T13:00:00Z",
        duracao_min=90,
        descricao="Sem interrupções",
    )
    assert result["success"] is True
    assert result["uid"]
    [evento] = parse_ics(source.read_text(encoding="utf-8"))
    assert evento.summary == "Foco profundo"
    assert evento.start == datetime(2026, 9, 21, 13, 0, tzinfo=UTC)
    assert evento.end == datetime(2026, 9, 21, 14, 30, tzinfo=UTC)


def test_add_exige_fim_ou_duracao(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(subcommand="add", titulo="Sem fim", inicio="2026-09-21T13:00:00Z")
    assert result["success"] is False
    assert "fim ou duracao_min" in result["error"]


def test_add_recusa_fim_e_duracao_juntos(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    result = calendar_tool(
        subcommand="add",
        titulo="Conflito",
        inicio="2026-09-21T13:00:00Z",
        fim="2026-09-21T14:00:00Z",
        duracao_min=30,
    )
    assert result["success"] is False
    assert "não ambos" in result["error"]


def test_add_sem_titulo_recusa(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    for titulo in (None, "", "   "):
        result = calendar_tool(
            subcommand="add", titulo=titulo, inicio="2026-09-21T13:00:00Z", duracao_min=30
        )
        assert result["success"] is False


def test_add_em_diretorio_recusa_com_honestidade(home):
    pasta = home / "agenda-dir"
    pasta.mkdir()
    _write_calendar(home, str(pasta))
    result = calendar_tool(
        subcommand="add", titulo="X", inicio="2026-09-21T13:00:00Z", duracao_min=30
    )
    assert result["success"] is False
    assert "diretório é leitura" in result["error"]


def test_rm_remove_por_uid_e_uid_ausente_nao_finge(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(_vevent(UID_A, "20260921T100000Z", "Um")), encoding="utf-8")
    _write_calendar(home, str(source))
    antes = source.read_text(encoding="utf-8")
    falha = calendar_tool(subcommand="rm", uid="nao-existe")
    assert falha["success"] is False
    assert "não encontrado" in falha["error"]
    assert source.read_text(encoding="utf-8") == antes
    ok = calendar_tool(subcommand="rm", uid=UID_A)
    assert ok["success"] is True
    assert ok["removidos"] == 1
    assert parse_ics(source.read_text(encoding="utf-8")) == []


def test_fonte_some_apos_o_gate_recusa_nomeado(home):
    source = home / "agenda.ics"
    source.write_text(_vcalendar(), encoding="utf-8")
    _write_calendar(home, str(source))
    assert _calendar_available() is True
    source.unlink()
    result = calendar_tool(
        subcommand="range",
        desde="2026-09-21T00:00:00Z",
        ate="2026-09-22T00:00:00Z",
        limite=50,
    )
    assert result["success"] is False
    assert "não existe" in result["error"]


def test_aprovacao_por_subcomando():
    assert needs_tool_approval("calendar", {"subcommand": "add"}) is True
    assert needs_tool_approval("calendar", {"subcommand": "rm"}) is True
    assert needs_tool_approval("calendar", '{"subcommand":"add","titulo":"x"}') is True
    assert needs_tool_approval("calendar", {"subcommand": "today"}) is False
    assert needs_tool_approval("calendar", {"subcommand": "range"}) is False
    assert needs_tool_approval("calendar") is False
