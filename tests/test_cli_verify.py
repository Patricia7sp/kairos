"""O comando `kairos verify` end-to-end via CLI."""

from __future__ import annotations

import json

from kairos_cli.handlers import ExitCode
from kairos_cli.main import main
from kairos_cli.verify_recipe import manifest_path


def _makefile(root, body):
    marca = root / "Makefile"
    marca.write_text(body + "\n", encoding="utf-8")
    return root


def _receita_make(root, alvo="echo ok"):
    return _makefile(
        root,
        f"test:\n\t{alvo}\ntargets:\n\techo {alvo}\n",
    )


def test_verify_projeto_reconhecido_eh_verde(tmp_path, capsys):
    _receita_make(tmp_path)
    assert main(["verify", "--path", str(tmp_path), "--skip-start"]) == ExitCode.OK
    saida = capsys.readouterr().out
    assert "Resultado: OK" in saida
    assert "Receita: Makefile-driven project" in saida
    assert "test" in saida


def test_verify_fase_falha_vira_falha(tmp_path, capsys):
    _receita_make(tmp_path, alvo="exit 3")
    assert main(["verify", "--path", str(tmp_path), "--skip-start"]) == ExitCode.ERROR
    result = capsys.readouterr()
    assert "Resultado: FALHOU" in result.out
    assert "FALHA" in result.out


def test_verify_sem_receita_recusa_e_aponta_manifesto(tmp_path, capsys):
    assert main(["verify", "--path", str(tmp_path)]) == ExitCode.ERROR
    result = capsys.readouterr()
    assert "Nenhum projeto reconhecível" in result.err
    assert "environment.json" in result.err


def test_verify_nao_diretorio_eh_uso(tmp_path, capsys):
    arquivo = tmp_path / "arquivo.txt"
    arquivo.write_text("x", encoding="utf-8")
    assert main(["verify", "--path", str(arquivo)]) == ExitCode.USAGE
    assert "não é um diretório" in capsys.readouterr().err


def test_verify_json_shape(tmp_path, capsys):
    _receita_make(tmp_path, alvo="exit 7")
    assert main(["verify", "--path", str(tmp_path), "--skip-start", "--json"]) == ExitCode.ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["recipe"] == "Makefile-driven project"
    fases_ok = [p["ok"] for p in payload["phases"]]
    assert False in fases_ok


def test_verify_json_sucesso(tmp_path, capsys):
    _receita_make(tmp_path)
    assert main(["verify", "--path", str(tmp_path), "--skip-start", "--json"]) == ExitCode.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True


def test_verify_detect_only_imprime_receita(tmp_path, capsys):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n', encoding="utf-8")
    assert main(["verify", "--path", str(tmp_path), "--detect-only"]) == ExitCode.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "detected"
    assert payload["recipe"]["kind"] == "rust"


def test_verify_detect_only_json_em_linha_unica(tmp_path, capsys):
    (tmp_path / "Cargo.toml").write_text('[package]\nname="x"\n', encoding="utf-8")
    assert main(["verify", "--path", str(tmp_path), "--detect-only", "--json"]) == ExitCode.OK
    saida = capsys.readouterr().out
    assert json.loads(saida)["recipe"]["kind"] == "rust"
    assert "\n" not in saida.strip()


def test_verify_save_grava_manifesto(tmp_path, capsys):
    _makefile(tmp_path, "test:\n\techo ok\n")
    assert main(["verify", "--path", str(tmp_path), "--save", "--skip-start"]) == ExitCode.OK
    saida = capsys.readouterr().out
    assert ".kairos" in saida and "environment.json" in saida
    assert manifest_path(tmp_path).exists()
    # na segunda passada o manifesto é a origem
    assert main(["verify", "--path", str(tmp_path), "--detect-only"]) == ExitCode.OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "manifest"


def test_verify_phase_selecionada_so_roda_aquela(tmp_path, capsys):
    _makefile(tmp_path, "build:\n\techo building\n")
    assert (
        main(["verify", "--path", str(tmp_path), "--phase", "build", "--skip-start"]) == ExitCode.OK
    )
    saida = capsys.readouterr().out
    assert "build" in saida
    assert "(nenhuma fase executada)" not in saida


def test_verify_timeout_vira_timedout(tmp_path, capsys):
    _makefile(
        tmp_path,
        "test:\n\tsleep 60\ntarget2:\n\techo outro\n",
    )
    assert (
        main(["verify", "--path", str(tmp_path), "--timeout", "0.2", "--skip-start"])
        == ExitCode.ERROR
    )
    saida = capsys.readouterr().out
    assert "TIMEOUT" in saida
