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
    assert "runtime-worker-image" not in services
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


@pytest.mark.parametrize("transicao", [True, False])
def test_https_privado_restringe_bind_e_cookie_em_cada_etapa(monkeypatch, transicao):
    from fastapi.testclient import TestClient

    from kairos_web.server import SESSION_TOKEN, app

    command = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.https.yaml"]
    if transicao:
        command.extend(["-f", "compose.https-transicao.yaml"])
    result = subprocess.run(
        [*command, "config"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    service = yaml.safe_load(result.stdout)["services"]["kairos"]
    assert {(p["host_ip"], str(p["published"]), p["target"]) for p in service["ports"]} == (
        {("127.0.0.1", "9119", 9119), ("100.87.25.101", "9119", 9119)}
        if transicao
        else {("127.0.0.1", "9119", 9119)}
    )
    monkeypatch.setenv(
        "KAIROS_WEB_HTTP_COOKIE_HOST", service["environment"]["KAIROS_WEB_HTTP_COOKIE_HOST"]
    )

    async def backend(scope, receive, send):
        # O navegador usa o esquema externo; após o Serve o backend recebe HTTP.
        await app({**scope, "scheme": "http"}, receive, send)

    for host, secure in (
        ("kairos.example.test", True),
        ("100.87.25.101:9119", not transicao),
    ):
        origin = f"https://{host}" if secure else f"http://{host}"
        with TestClient(backend, base_url=origin) as client:
            res = client.post("/api/auth/login", json={"token": SESSION_TOKEN})
            assert res.status_code == 200
            (cookie,) = client.cookies.jar
            assert cookie.secure is secure
            assert not cookie.domain_specified
            http_me = client.get(f"http://{host}/api/auth/me").json()
            assert http_me["authenticated"] is not secure
            assert client.get("/api/auth/me").json()["authenticated"] is True
            assert not client.get("https://outro.example.test/api/auth/me").json()["authenticated"]
            res = client.post("/api/auth/logout")
            assert res.status_code == 200
            assert ("secure" in res.headers["set-cookie"].lower()) is secure
            assert not list(client.cookies.jar)


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


def test_proxy_https_tem_identidade_propria_e_alcanca_apenas_a_web_na_bridge_compartilhada():
    def compose(*files):
        flags = [arg for file in files for arg in ("-f", file)]
        result = subprocess.run(
            ["docker", "compose", *flags, "--profile", "agent-runtime", "config"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        return yaml.safe_load(result.stdout)

    proxy_stack = compose("compose.https-proxy.yaml")
    app_stack = compose("compose.yaml", "compose.https.yaml")
    assert proxy_stack["name"] != app_stack["name"]
    proxy = proxy_stack["services"]["proxy"]
    assert "--tun=userspace-networking" in proxy["command"]
    forbidden = ("ports", "privileged", "devices", "cap_add", "network_mode")
    assert not any(proxy.get(k) for k in forbidden)
    assert proxy["read_only"] and "ALL" in proxy["cap_drop"]
    assert "no-new-privileges:true" in proxy["security_opt"]
    (state,) = proxy["volumes"]
    assert state["type"] == "volume" and state["target"] == "/var/lib/tailscale"
    identity = proxy_stack["volumes"][state["source"]]["name"]
    assert all(volume["name"] != identity for volume in app_stack["volumes"].values())

    proxy_nets = [proxy_stack["networks"][net] for net in proxy["networks"]]
    (backend,) = [net["name"] for net in proxy_nets if net.get("internal")]
    assert any(not net.get("internal") for net in proxy_nets)  # saída para o tailnet
    proxy_names = {net["name"] for net in proxy_nets}
    shared = [
        (service, app_stack["networks"][net], attachment)
        for service, spec in app_stack["services"].items()
        for net, attachment in spec.get("networks", {}).items()
        if app_stack["networks"][net]["name"] in proxy_names
    ]
    ((service, network, attachment),) = shared
    assert service == "kairos" and network["external"] and network["name"] == backend
    assert "kairos-web.invalid" in attachment["aliases"]


def test_config_semeada_desabilita_runtime_com_codex_pinned():
    config = yaml.safe_load((ROOT / "docker/config.default.yaml").read_text(encoding="utf-8"))

    assert config["agent_runtime"] == {
        "enabled": False,
        "codex_binary": "/usr/local/bin/codex",
        "codex_version": "0.154.0",
        "allowed_directories": [],
        "project_catalogs": [],
        "broad_access_enabled": False,
    }


@pytest.mark.parametrize("catalog", [None, "/srv/catalog"])
def test_catalog_mount_is_readonly_broker_only_with_existing_project_fallback(catalog):
    env = {**os.environ, "KAIROS_RUNTIME_PROJECT": "/srv/existing"}
    env.pop("KAIROS_RUNTIME_PROJECT_CATALOG", None)
    if catalog:
        env["KAIROS_RUNTIME_PROJECT_CATALOG"] = catalog
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
    mount = next(
        m for m in services["runtime-broker"]["volumes"] if m["target"] == "/projects/versions"
    )
    assert mount["source"] == (catalog or "/srv/existing")
    assert mount["read_only"] is True
    assert not mount.get("bind", {}).get("create_host_path", False)
    assert all(m["target"] != "/projects/versions" for m in services["kairos"]["volumes"])


@pytest.mark.parametrize("image", [None, "sha256:" + "a" * 64])
def test_runtime_retains_configured_worker_image_without_exposing_host_resources(image):
    env = {**os.environ, "COMPOSE_PROFILES": ""}
    env.pop("KAIROS_RUNTIME_WORKER_IMAGE", None)
    if image is not None:
        env["KAIROS_RUNTIME_WORKER_IMAGE"] = image
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
    holder = services["runtime-worker-image"]
    assert holder["image"] == (image or "kairos:external-sandbox")
    assert holder["pull_policy"] == "never"
    assert holder["platform"] == "linux/amd64"
    assert not holder.get("build")
    assert holder["container_name"] == "kairos-runtime-worker-image"
    assert holder["entrypoint"] == ["/bin/sleep"]
    assert holder["command"] == ["infinity"]
    assert holder["restart"] == "unless-stopped"
    assert holder["user"] == "10000:10000"
    assert holder["network_mode"] == "none"
    assert holder["read_only"] is True
    assert holder["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in holder["security_opt"]
    assert not holder.get("privileged", False)
    assert not any(
        holder.get(key) for key in ("ports", "volumes", "secrets", "environment", "cap_add")
    )
    assert holder["pids_limit"] == 8
    assert int(holder["mem_limit"]) == 16 * 1024 * 1024
    assert float(holder["cpus"]) == 0.05
    dependencies = services["runtime-broker"]["depends_on"]
    assert dependencies["runtime-worker-image"]["condition"] == "service_started"
    assert dependencies["kairos"]["condition"] == "service_healthy"
