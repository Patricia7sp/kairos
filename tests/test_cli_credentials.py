"""CLI e Web compartilham credenciais consumidas pelo gateway real."""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from kairos_cli.main import main
from kairos_providers.composition import build_provider_gateway
from kairos_providers.contracts import ProviderModelRef
from kairos_security.credentials import (
    CredentialNotFoundError,
    CredentialRef,
    CredentialSecret,
    ExternalCredentialSource,
    build_credential_service,
)
from kairos_web.server import SESSION_TOKEN, TOKEN_HEADER, app


@pytest.fixture
def home(tmp_path, monkeypatch):
    passphrase = tmp_path / "passphrase"
    passphrase.write_text("credential-test-passphrase")
    passphrase.chmod(0o600)
    monkeypatch.setenv("KAIROS_HOME", str(tmp_path))
    monkeypatch.setenv("KAIROS_DISABLE_KEYRING", "1")
    monkeypatch.setenv("KAIROS_VAULT_PASSPHRASE_FILE", str(passphrase))
    monkeypatch.setattr(app.state, "kairos_home", tmp_path, raising=False)
    return tmp_path


def login():
    return main(["login", "--provider", "openai", "--api-key", "cli-private-value"])


def test_login_e_consumido_pelo_gateway_sem_segredo_no_perfil(home, capsys):
    assert login() == 0
    document = json.loads((home / "auth.json").read_text())
    assert document["credential_pool"]["openai"] == [
        {"credential_id": "primary", "auth_method": "api_key"}
    ]
    assert "cli-private-value" not in (home / "auth.json").read_text()
    assert "cli-private-value" not in capsys.readouterr().out

    async def prepare():
        gateway = build_provider_gateway(home)
        try:
            prepared = gateway.prepare(ProviderModelRef("openai", "gpt-4.1"))
            assert prepared.credential_id == "primary"
        finally:
            await gateway.aclose()

    asyncio.run(prepare())
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "cli-private-value"
    }


def test_logout_remove_segredos_do_provedor_preservando_outros(home):
    assert login() == 0
    vault = build_credential_service(home)
    vault.put(CredentialRef("openai", "other"), CredentialSecret({"api_key": "other-key"}))
    vault.put(CredentialRef("groq", "primary"), CredentialSecret({"api_key": "groq-key"}))
    path = home / "auth.json"
    document = json.loads(path.read_text())
    document["revision"] = 37
    document["credential_pool"]["openai"].append({"credential_id": "other"})
    document["credential_pool"]["groq"] = [{"credential_id": "primary"}]
    path.write_text(json.dumps(document))
    assert main(["logout", "--provider", "openai"]) == 0
    vault = build_credential_service(home)
    assert vault.list("openai") == []
    assert vault.get(CredentialRef("groq", "primary"))
    document["credential_pool"].pop("openai")
    assert json.loads(path.read_text()) == document


def test_logout_sem_provedor_remove_todas_as_credenciais_locais(home):
    assert login() == 0
    vault = build_credential_service(home)
    vault.put(CredentialRef("groq", "primary"), CredentialSecret({"api_key": "groq-key"}))
    assert main(["logout"]) == 0
    assert build_credential_service(home).list() == []
    assert json.loads((home / "auth.json").read_text())["credential_pool"] == {}


def test_cli_e_web_compartilham_salvar_e_remover(home):
    client = TestClient(app, headers={TOKEN_HEADER: SESSION_TOKEN})
    assert login() == 0
    assert client.delete("/api/providers/openai/credentials").status_code == 200
    with pytest.raises(CredentialNotFoundError):
        build_credential_service(home).get(CredentialRef("openai", "primary"))
    assert (
        client.post(
            "/api/providers/openai/credentials", json={"secret": "web-private-value"}
        ).status_code
        == 200
    )
    assert main(["logout", "--provider", "openai"]) == 0
    assert build_credential_service(home).list("openai") == []


@pytest.mark.parametrize("command", ["login", "logout"])
def test_cofre_bloqueado_nao_reporta_sucesso(home, monkeypatch, command):
    vault = build_credential_service(home)
    vault.put(CredentialRef("openai", "primary"), CredentialSecret({"api_key": "retained"}))
    monkeypatch.delenv("KAIROS_VAULT_PASSPHRASE_FILE")
    code = login() if command == "login" else main(["logout", "--provider", "openai"])
    assert code != 0
    assert vault.get(CredentialRef("openai", "primary")).reveal() == {"api_key": "retained"}
    assert not (home / "auth.json").exists()


def test_login_rejeita_provedor_desconhecido(home):
    assert main(["login", "--provider", "absent", "--api-key", "private-value"]) != 0
    assert not (home / "auth.json").exists()


def test_login_preserva_documento_corrompido_e_segredo_anterior(home):
    vault = build_credential_service(home)
    vault.put(CredentialRef("openai", "primary"), CredentialSecret({"api_key": "retained"}))
    (home / "auth.json").write_text("[invalid")
    assert login() != 0
    assert (home / "auth.json").read_text() == "[invalid"
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "retained"
    }


@pytest.mark.parametrize("command", ["login", "logout"])
def test_falha_de_metadados_restaura_segredo_e_metodo(home, monkeypatch, command):
    from kairos_security.credentials import io

    vault = build_credential_service(home)
    vault.put(
        CredentialRef("openai", "primary"),
        CredentialSecret({"api_key": "retained"}),
        auth_method="retained-method",
    )
    original = json.dumps(
        {"revision": 7, "credential_pool": {"openai": [{"credential_id": "primary"}]}}
    )
    (home / "auth.json").write_text(original)
    writer = io.secure_atomic_write_text

    def fail_auth(path, content):
        if path.name == "auth.json":
            raise OSError("disk unavailable")
        writer(path, content)

    monkeypatch.setattr(io, "secure_atomic_write_text", fail_auth)
    code = login() if command == "login" else main(["logout", "--provider", "openai"])
    assert code != 0
    assert (home / "auth.json").read_text() == original
    vault = build_credential_service(home)
    assert vault.get(CredentialRef("openai", "primary")).reveal() == {"api_key": "retained"}
    assert vault.list("openai")[0].auth_method == "retained-method"


@pytest.mark.parametrize("command", ["login", "logout"])
def test_fonte_externa_nao_pode_ser_alterada(home, monkeypatch, command):
    from kairos_security.credentials import factory

    ref = CredentialRef("openai", "primary")
    source = ExternalCredentialSource({ref: CredentialSecret({"api_key": "external-private"})})
    monkeypatch.setattr(factory, "build_credential_service", lambda _: source)
    code = login() if command == "login" else main(["logout", "--provider", "openai"])
    assert code != 0
    assert source.get(ref).reveal() == {"api_key": "external-private"}
    assert not (home / "auth.json").exists()


def test_logout_restaura_exclusao_que_falha_apos_efeito_real(home, monkeypatch):
    from kairos_security.credentials import CredentialService

    assert login() == 0
    original_delete = CredentialService.delete

    def fail_after_delete(service, ref):
        original_delete(service, ref)
        raise OSError("index unavailable after deletion")

    monkeypatch.setattr(CredentialService, "delete", fail_after_delete)
    assert main(["logout", "--provider", "openai"]) != 0
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "cli-private-value"
    }


def test_login_pede_chave_sem_expo_la_em_argumentos(home, monkeypatch, capsys):
    import io

    class Terminal(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Terminal())
    monkeypatch.setattr("getpass.getpass", lambda _: "prompt-private")
    assert main(["login", "--provider", "openai"]) == 0
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "prompt-private"
    }
    assert "prompt-private" not in capsys.readouterr().out


def test_login_sem_chave_e_sem_terminal_recusa_sem_consumir_entrada(home, monkeypatch):
    import io

    stream = io.StringIO("private-pipe")
    monkeypatch.setattr("sys.stdin", stream)
    assert main(["login", "--provider", "openai"]) != 0
    assert stream.tell() == 0
    assert not (home / "auth.json").exists()


def test_login_restaura_gravacao_no_cofre_que_falha_apos_efeito(home, monkeypatch):
    from kairos_security.credentials import CredentialService

    assert login() == 0
    original = (home / "auth.json").read_text()
    put = CredentialService.put
    failed = False

    def fail_once(service, ref, secret, *, auth_method="api_key"):
        nonlocal failed
        result = put(service, ref, secret, auth_method=auth_method)
        if not failed:
            failed = True
            raise OSError("vault unavailable after writing")
        return result

    monkeypatch.setattr(CredentialService, "put", fail_once)
    assert main(["login", "--provider", "openai", "--api-key", "replacement-private"]) != 0
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "cli-private-value"
    }
    assert (home / "auth.json").read_text() == original


@pytest.mark.parametrize("command", ["login", "logout"])
def test_falha_apos_substituir_metadados_restaura_documento(home, monkeypatch, command):
    from kairos_security.credentials import io

    assert login() == 0
    path = home / "auth.json"
    path.write_text(json.dumps(json.loads(path.read_text()), separators=(",", ":")))
    original = path.read_text()
    writer = io.secure_atomic_write_text
    failed = False

    def fail_after_write(path, content):
        nonlocal failed
        writer(path, content)
        if path.name == "auth.json" and not failed:
            failed = True
            raise OSError("directory sync failed")

    monkeypatch.setattr(io, "secure_atomic_write_text", fail_after_write)
    code = login() if command == "login" else main(["logout", "--provider", "openai"])
    assert code != 0
    assert path.read_text() == original
    assert build_credential_service(home).get(CredentialRef("openai", "primary")).reveal() == {
        "api_key": "cli-private-value"
    }


@pytest.mark.parametrize("command", ["login", "logout"])
def test_erro_de_backend_nao_expoe_material_secreto(home, monkeypatch, capsys, command):
    from test_credential_vault import MemoryKeyring

    from kairos_security.credentials import (
        CredentialService,
        EncryptedFileVault,
        SystemKeyringVault,
        factory,
    )

    client = MemoryKeyring()
    service = CredentialService(
        keyring=SystemKeyringVault(client, index_path=home / "credential-index.json"),
        encrypted=EncryptedFileVault(home / "unused.vault"),
    )
    monkeypatch.setattr(factory, "build_credential_service", lambda _: service)
    assert login() == 0
    capsys.readouterr()

    def fail(*_):
        raise RuntimeError("backend rejected sensitive-private-value")

    method = "set_password" if command == "login" else "delete_password"
    monkeypatch.setattr(client, method, fail)
    code = login() if command == "login" else main(["logout", "--provider", "openai"])
    assert code != 0
    output = capsys.readouterr()
    assert "sensitive-private-value" not in output.out + output.err
