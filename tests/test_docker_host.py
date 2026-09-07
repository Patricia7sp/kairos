import asyncio
import json
import sys

import pytest
import yaml

from kairos_runtime.errors import RuntimeErrorInfo
from kairos_runtime.host import _load_config


def config(home, **values):
    (home / "config.yaml").write_text(yaml.safe_dump({"agent_runtime": values}))
    return _load_config(home)


def test_default_backend_remains_local(tmp_path):
    assert config(tmp_path).backend == "local"


def test_docker_configuration_is_explicit_and_bounded(tmp_path):
    value = config(
        tmp_path, backend="docker", docker_image="kairos:external-sandbox", max_workers=2
    )
    assert value.backend == "docker"
    assert value.docker_image == "kairos:external-sandbox"
    assert value.model == "gpt-5.5" and value.max_workers == 2


@pytest.mark.parametrize(
    "values",
    [
        {"backend": "unknown"},
        {"backend": "docker", "broad_access_enabled": True},
        {"backend": "docker", "max_workers": 0},
        {"backend": "docker", "max_workers": True},
        {"backend": "docker", "max_workers": 65},
        {"backend": "docker", "docker_image": "-bad"},
        {"backend": "docker", "model": ""},
    ],
)
def test_invalid_backend_configuration_is_rejected(tmp_path, values):
    with pytest.raises(RuntimeErrorInfo):
        config(tmp_path, **values)


@pytest.mark.parametrize("relative", [".", "..", "codex-runtime", "docker-sessions", "../alias"])
def test_broker_secrets_cannot_overlap_allowed_projects(tmp_path, relative):
    broker = tmp_path / "broker"
    broker.mkdir()
    target = broker / relative
    if relative == "../alias":
        target.symlink_to(broker, target_is_directory=True)
    else:
        target.mkdir(exist_ok=True)
    with pytest.raises(RuntimeErrorInfo):
        config(broker, backend="docker", allowed_directories=[str(target)])


def test_sibling_project_is_allowed(tmp_path):
    broker, project = tmp_path / "broker", tmp_path / "project"
    broker.mkdir()
    project.mkdir()
    assert config(
        broker, backend="docker", allowed_directories=[str(project)]
    ).allowed_directories == (str(project),)


def test_docker_host_exposes_dedicated_account_flow_over_unix_client(tmp_path, monkeypatch):
    from kairos_runtime.client import RuntimeClient
    from kairos_runtime.host import serve_runtime

    binary = tmp_path / "fake-codex"
    evidence = tmp_path / "process.json"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, json\n"
        "if '--version' in sys.argv:\n"
        " print('codex-cli 0.153.4'); sys.exit(0)\n"
        f"with open({str(evidence)!r},'w') as f:\n"
        " json.dump({'args':sys.argv,'cwd':os.getcwd(),'env':dict(os.environ)},f)\n"
        "for line in sys.stdin:\n"
        " m=json.loads(line)\n"
        " if 'id' not in m: continue\n"
        " method=m['method']\n"
        " if method=='initialize':\n"
        "  r={'codexHome':os.environ['CODEX_HOME'],'platformOs':'linux','platformFamily':'unix',"
        "'userAgent':'kairos/0.153.4'}\n"
        " elif method=='account/read': r={'requiresOpenaiAuth':True,'account':None}\n"
        " elif method=='account/login/start':\n"
        "  r={'type':'chatgptDeviceCode','loginId':'dedicated-login','userCode':'TEST-CODE',"
        "'verificationUrl':'https://auth.openai.com/codex/device'}\n"
        " else: r={}\n"
        " print(json.dumps({'id':m['id'],'result':r}),flush=True)\n"
    )
    binary.chmod(0o700)
    broker = tmp_path / "broker"
    broker.mkdir()
    config(broker, enabled=True, backend="docker", codex_binary=str(binary))
    # The external backend replaces the nested sandbox probe with worker attestation.
    (broker / ".container-mode").write_text("runtime=s6\n")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-secret-must-not-cross")
    monkeypatch.setenv("UNLISTED_SECRET", "synthetic-secret-must-not-cross")

    async def scenario():
        task = asyncio.create_task(serve_runtime(broker))
        client = RuntimeClient(broker / "run" / "runtime.sock")
        try:
            async with asyncio.timeout(5):
                while not client.socket_path.exists():
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            assert (await client.status())["state"] == "ready"
            assert (await client.account_status())["authenticated"] is False
            login = await client.account_login("chatgptDeviceCode")
            assert login["user_code"] == "TEST-CODE"
            with pytest.raises(RuntimeErrorInfo):
                await client.account_login("apiKey", "never-forward")
            observed = json.loads(evidence.read_text())
            home = str(broker / "codex-runtime")
            assert (
                observed["cwd"] == observed["env"]["CODEX_HOME"] == observed["env"]["HOME"] == home
            )
            assert "synthetic-secret-must-not-cross" not in json.dumps(observed)
            assert 'cli_auth_credentials_store="file"' in observed["args"]
        finally:
            await client.aclose()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert not client.socket_path.exists()

    asyncio.run(scenario())
