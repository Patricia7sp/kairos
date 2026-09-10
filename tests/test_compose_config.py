import os
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("repository", [None, "/srv/projects/kairos"])
def test_broker_mounts_real_repository_readonly_without_expanding_web_access(repository):
    env = {**os.environ, "KAIROS_RUNTIME_PROJECT": "/srv/projects/synthetic"}
    env.pop("KAIROS_RUNTIME_KAIROS_PROJECT", None)
    if repository is not None:
        env["KAIROS_RUNTIME_KAIROS_PROJECT"] = repository
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "compose.yaml"),
            "--profile",
            "agent-runtime",
            "config",
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    services = yaml.safe_load(result.stdout)["services"]
    mounts = {m["target"]: m for m in services["runtime-broker"]["volumes"]}
    assert mounts["/projects/current"]["source"] == "/srv/projects/synthetic"
    assert mounts["/projects/kairos"]["source"] == (repository or "/srv/projects/synthetic")
    assert mounts["/projects/kairos"]["read_only"] is True
    assert mounts["/projects/kairos"].get("bind", {}).get("create_host_path", False) is False
    assert all(m["target"] != "/projects/kairos" for m in services["kairos"]["volumes"])


def test_broker_externo_e_opt_in_e_compartilha_estado_sem_expor_socket_na_web():
    env = {**os.environ, "COMPOSE_PROFILES": "", "KAIROS_RUNTIME_EXTERNAL": "0"}
    command = ["docker", "compose", "-f", str(ROOT / "compose.yaml")]
    default = subprocess.run(
        [*command, "config"], env=env, check=True, capture_output=True, text=True
    )
    services = yaml.safe_load(default.stdout)["services"]
    assert "runtime-broker" not in services
    assert services["kairos"]["environment"].get("KAIROS_RUNTIME_EXTERNAL") == "0"

    env.update(KAIROS_RUNTIME_EXTERNAL="1", KAIROS_DOCKER_GID="983")
    enabled = subprocess.run(
        [*command, "--profile", "agent-runtime", "config"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    services = yaml.safe_load(enabled.stdout)["services"]
    app, broker = services["kairos"], services["runtime-broker"]
    assert app["environment"]["KAIROS_RUNTIME_EXTERNAL"] == "1"
    assert broker["user"] == "10000:10000"
    assert "983" in broker["group_add"]
    assert not broker.get("privileged", False)
    assert not broker.get("ports")
    assert broker["read_only"] is True
    assert "ALL" in broker["cap_drop"]
    assert "no-new-privileges:true" in broker["security_opt"]
    assert all(m["target"] != "/var/run/docker.sock" for m in app["volumes"])
    mounts = {m["target"]: m for m in broker["volumes"]}
    assert mounts["/var/run/docker.sock"]["source"] == "/var/run/docker.sock"
    assert mounts["/opt/data"]["source"] == "kairos-data"
    assert mounts["/projects/current"]["read_only"] is True
    # Algumas versões do Compose omitem valores false no YAML normalizado.
    assert mounts["/projects/current"].get("bind", {}).get("create_host_path", False) is False


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
