import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_compose_configura_fallback_criptografado_com_segredo_somente_leitura():
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "config"],
        check=True,
        capture_output=True,
        text=True,
    )
    service = yaml.safe_load(result.stdout)["services"]["kairos"]

    assert service["environment"]["KAIROS_DISABLE_KEYRING"] == "1"
    assert service["environment"]["KAIROS_VAULT_PASSPHRASE_FILE"] == (
        "/run/secrets/kairos-vault-passphrase"  # noqa: S105
    )
    passphrase_mount = next(
        mount
        for mount in service["volumes"]
        if mount["target"] == "/run/secrets/kairos-vault-passphrase"
    )
    assert passphrase_mount["type"] == "bind"
    assert passphrase_mount["source"] == "/etc/komodo/secrets/kairos-vault-passphrase"
    assert passphrase_mount["read_only"] is True


def test_compose_nao_eleva_runtime_nem_expoe_docker_socket():
    result = subprocess.run(
        ["docker", "compose", "-f", str(ROOT / "compose.yaml"), "config"],
        check=True,
        capture_output=True,
        text=True,
    )
    service = yaml.safe_load(result.stdout)["services"]["kairos"]

    assert service["platform"] == "linux/amd64"
    assert service.get("privileged", False) is False
    assert service.get("cap_add", []) == []
    assert all(mount["target"] != "/var/run/docker.sock" for mount in service["volumes"])
    data_mount = next(mount for mount in service["volumes"] if mount["target"] == "/opt/data")
    assert data_mount["type"] == "volume"
    assert data_mount["source"] == "kairos-data"


def test_config_semeada_desabilita_runtime_com_codex_pinned():
    config = yaml.safe_load((ROOT / "docker/config.default.yaml").read_text(encoding="utf-8"))

    assert config["agent_runtime"] == {
        "enabled": False,
        "codex_binary": "/usr/local/bin/codex",
        "codex_version": "0.153.4",
        "allowed_directories": [],
        "broad_access_enabled": False,
    }
