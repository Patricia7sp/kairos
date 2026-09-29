"""Lote da CLI oficial: shell interativo, `config set/edit`, `kairos context`
e a resolução única de `KAIROS_HOME`.

O shell delega ao `main` — os testes injetam um `evaluate` fake que registra
argv, sem montar service graph. O guard de TTY preserva `main([]) == USAGE`
fora de terminal (já travado em test_cli_surface), então aqui ele é testado
de forma direta.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kairos_cli.config import (
    get_config_path,
    load_config,
    open_in_editor,
    parse_cli_value,
    set_config_value,
)
from kairos_cli.context import build_context, render_context
from kairos_cli.main import main
from kairos_cli.shell import run_shell, should_open_shell
from kairos_cli.startup_fast import resolve_kairos_home


class _Tty(io.StringIO):
    def __init__(self, *, tty: bool, text: str = "") -> None:
        super().__init__(text)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def _roda_shell(*linhas: str, evaluate=None, **kwargs) -> int:
    return run_shell(
        evaluate=evaluate,
        stdin=_Tty(tty=True, text="\n".join(linhas) + "\n"),
        stdout=io.StringIO(),
        command_names=("run", "chat", "context", "config"),
        **kwargs,
    )


class _Registrador:
    def __init__(self):
        self.vistos = []

    def __call__(self, argv):
        self.vistos.append(argv)
        return 0


# --- guard de TTY ----------------------------------------------------------


def test_shell_so_abre_em_terminal_de_verdade():
    assert should_open_shell(stdin=_Tty(tty=True), stdout=_Tty(tty=True)) is True
    assert should_open_shell(stdin=_Tty(tty=True), stdout=_Tty(tty=False)) is False
    assert should_open_shell(stdin=_Tty(tty=False), stdout=_Tty(tty=True)) is False


def test_main_sem_comando_fora_de_tty_continua_ajuda_mais_usages(capsys):
    assert main([]) == 2
    saida = capsys.readouterr().out
    assert "usage" in saida.lower()


def test_main_sem_comando_com_json_nao_abre_shell(capsys):
    assert main(["--json"]) == 2
    assert "Kairos CLI" not in capsys.readouterr().out


# --- delegação do menu -----------------------------------------------------


def test_menu_item_context_delega_argv_correto():
    reg = _Registrador()
    _roda_shell("3", "sair", evaluate=reg)
    assert reg.vistos == [["context"]]


def test_menu_item_run_carrega_sessao_e_cwd_do_shell():
    reg = _Registrador()
    _roda_shell("1", "sair", evaluate=reg, cwd="/work")
    assert reg.vistos == [["run", "analise o projeto atual em /work", "--session", "cli"]]


def test_menu_item_chat_passa_sessao_do_shell():
    reg = _Registrador()
    _roda_shell("2", "0", evaluate=reg)
    assert reg.vistos == [["chat", "--session", "cli"]]


def test_sessao_do_shell_vem_do_ambiente():
    reg = _Registrador()
    run_shell(
        evaluate=reg,
        stdin=_Tty(tty=True, text="1\nsair\n"),
        stdout=io.StringIO(),
        cwd="/work",
        env={"KAIROS_SHELL_SESSION": "lab-42"},
        command_names=("run",),
    )
    assert reg.vistos == [["run", "analise o projeto atual em /work", "--session", "lab-42"]]


def test_linha_livre_com_comando_conhecido_vai_direto():
    reg = _Registrador()
    _roda_shell("config show", "sair", evaluate=reg)
    assert reg.vistos == [["config", "show"]]


def test_linha_livre_qualquer_coisa_e_prompt_rapido():
    reg = _Registrador()
    _roda_shell("me ajude a revisar", "sair", evaluate=reg)
    assert reg.vistos == [["run", "--session", "cli", "me", "ajude", "a", "revisar"]]


def test_chat_digitado_livre_ganha_sessao():
    reg = _Registrador()
    _roda_shell("chat", "sair", evaluate=reg)
    assert reg.vistos == [["chat", "--session", "cli"]]


def test_sair_nao_invoca_comando_algum():
    reg = _Registrador()
    assert _roda_shell("sair", evaluate=reg) == 0
    assert reg.vistos == []


def test_linha_vazia_ignorada_e_eof_encerra():
    reg = _Registrador()
    assert _roda_shell("", "sair", evaluate=reg) == 0
    assert reg.vistos == []


def test_retorno_nao_zero_do_comando_e_impresso_e_o_loop_continua():
    saida = io.StringIO()

    def evaluate(argv):
        return 3

    run_shell(
        evaluate=evaluate,
        stdin=_Tty(tty=True, text="context\nexit\n"),
        stdout=saida,
        command_names=("context",),
    )
    assert "(comando saiu com 3)" in saida.getvalue()


def test_system_exit_do_argparse_nao_derruba_o_shell():
    def evaluate(argv):
        raise SystemExit(2)

    assert _roda_shell("config", "sair", evaluate=evaluate) == 0


# --- config set ------------------------------------------------------------


def test_config_set_escreve_valor_tipado_e_preserva_o_resto(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("data_collection:\n  enabled: false\n")
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["config", "set", "chat.sandboxed_bash", "true"]) == 0
    cfg = load_config()
    assert cfg["chat"]["sandboxed_bash"] is True
    assert cfg["data_collection"]["enabled"] is False


def test_config_set_parseia_numeros_e_strings(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["config", "set", "ui.font_size", "16"]) == 0
    assert main(["config", "set", "ui.host", "localhost"]) == 0
    cfg = load_config()
    assert cfg["ui"]["font_size"] == 16
    assert cfg["ui"]["host"] == "localhost"


def test_parse_cli_value_so_interpreta_quando_nao_e_texto():
    assert parse_cli_value("true") is True
    assert parse_cli_value("42") == 42
    assert parse_cli_value("um texto livre") == "um texto livre"
    assert parse_cli_value("") is None


def test_set_config_value_cria_caminho_e_preserva_irmaos():
    cfg = {}
    set_config_value(cfg, "a.b.c", 1)
    set_config_value(cfg, "a.b.c", 2)
    set_config_value(cfg, "a.d", 3)
    assert cfg == {"a": {"b": {"c": 2}, "d": 3}}


# --- config edit -----------------------------------------------------------


def test_open_in_editor_monta_argv_com_editor_e_flags():
    capturado = []
    open_in_editor(
        Path("/tmp/x/config.yaml"), spawn=lambda a: capturado.append(a) or 0, editor="vi"
    )
    open_in_editor(
        Path("/tmp/x/config.yaml"),
        spawn=lambda a: capturado.append(a) or 0,
        editor="nano -w",
    )
    assert capturado == [
        ["vi", "/tmp/x/config.yaml"],
        ["nano", "-w", "/tmp/x/config.yaml"],
    ]


def test_open_in_editor_falha_vira_oserror():
    with pytest.raises(OSError, match="editor"):
        open_in_editor(Path("/tmp/x.yaml"), spawn=lambda a: 7, editor="vim")


def test_config_edit_main_chama_editor_e_sai_ok(tmp_path, monkeypatch, capsys):
    import kairos_cli.config as config_mod

    chamadas = []
    monkeypatch.setattr(
        config_mod, "open_in_editor", lambda path, **kw: chamadas.append(path) or None
    )
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["config", "edit"]) == 0
    assert chamadas == [tmp_path / "config.yaml"]
    assert f"config.yaml em {tmp_path / 'config.yaml'}" in capsys.readouterr().out


def test_config_edit_erro_vira_exit_code(tmp_path, monkeypatch):
    import kairos_cli.config as config_mod

    def explodir(*args, **kwargs):
        raise OSError("editor 'vim' encerrado com erro")

    monkeypatch.setattr(config_mod, "open_in_editor", explodir)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["config", "edit"]) == 1


# --- kairos context --------------------------------------------------------


def test_context_sem_state_db_diz_honesto_e_nao_cria_banco(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["context"]) == 0
    saida = capsys.readouterr().out
    assert "nenhum turno persistido ainda" in saida
    assert not (tmp_path / "state.db").exists()


def test_context_json_valido_sem_state_db(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["context", "--json"]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["state.db"] == {"presente": False}
    assert dados["home"] == str(tmp_path)


def test_context_com_state_db_mostra_sessao_real(tmp_path, monkeypatch, capsys):
    from kairos_state import connect, initialize_schema
    from kairos_state.repositories import SessionRepository

    db = connect(tmp_path / "state.db")
    try:
        initialize_schema(db)
        repo = SessionRepository(db)
        repo.create("cli", "cli")
        repo.create("web-abc", "web")
        with db:
            db.execute("UPDATE sessions SET message_count=5 WHERE id='web-abc'")
    finally:
        db.close()

    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert main(["context"]) == 0
    saida = capsys.readouterr().out
    assert "state.db       presente" in saida
    assert "2 sessões" in saida

    assert main(["context", "--json"]) == 0
    dados = json.loads(capsys.readouterr().out)
    assert dados["state.db"]["total_sessions"] == 2
    assert dados["state.db"]["sessao_mais_recente"]["id"] == "web-abc"
    assert dados["state.db"]["sessao_mais_recente"]["message_count"] == 5


def test_build_context_reporta_banco_corrompido(tmp_path):
    (tmp_path / "state.db").write_bytes(b"nao-e-sqlite")
    contexto = build_context(home=tmp_path)
    assert contexto["state.db"]["presente"] is True
    assert "erro" in contexto["state.db"]


def test_render_context_sem_estado():
    contexto = {
        "home": "/x",
        "versao": "0.1.0",
        "perfil": None,
        "container": False,
        "sessao_ativa": "cli-default",
        "config.yaml": True,
        "state.db": {"presente": False},
    }
    linhas = render_context(contexto)
    assert any("ausente" in linha for linha in linhas)


# --- unificação de KAIROS_HOME --------------------------------------------


def test_get_config_path_usa_a_mesma_resolucao_do_fast_path(tmp_path, monkeypatch):
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    assert get_config_path() == Path(resolve_kairos_home()) / "config.yaml"


def test_get_config_path_expande_til(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_HOME", "~/kairos-x")
    assert get_config_path().parent == tmp_path / "kairos-x"
