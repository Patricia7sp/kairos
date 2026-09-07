from __future__ import annotations

import asyncio
import copy
import io
import os
import tarfile

import pytest

from kairos_runtime.experimental.snapshot import snapshot_project
from kairos_runtime.experimental.worker_policy import WorkerPolicy


@pytest.fixture(autouse=True)
def docker_cli_for_lifecycle_units(monkeypatch):
    # Unit cases replace daemon operations; do not require Docker in the dev image.
    monkeypatch.setattr(
        "kairos_runtime.experimental.docker_worker.shutil.which", lambda _name: "/usr/bin/docker"
    )


def test_worker_removes_by_preallocated_name_when_create_is_cancelled(tmp_path, monkeypatch):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        removed = []

        async def start():
            worker._owned = True
            raise asyncio.CancelledError

        async def remove():
            removed.append(worker.name)
            worker._owned = False

        monkeypatch.setattr(worker, "_start", start)
        monkeypatch.setattr(worker, "_remove", remove)
        with pytest.raises(asyncio.CancelledError):
            await worker.__aenter__()
        assert removed == [worker.name]
        assert not worker._owned

    asyncio.run(scenario())


def test_worker_cleanup_failure_retains_ownership_for_retry(tmp_path, monkeypatch):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker._owned = True
        attempts = []

        async def remove():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("Docker indisponível")
            worker._owned = False

        monkeypatch.setattr(worker, "_remove", remove)
        with pytest.raises(RuntimeError):
            await worker.aclose()
        assert worker._owned
        await worker.aclose()
        assert not worker._owned

    asyncio.run(scenario())


def test_cancel_waits_for_daemon_create_before_removal(tmp_path, monkeypatch):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker.policy = WorkerPolicy("sha256:" + "a" * 64, worker.name)
        accepted, complete = asyncio.Event(), asyncio.Event()
        containers = set()

        async def daemon(*args):
            assert args[0] == "create"
            accepted.set()
            await complete.wait()
            containers.add(worker.name)

        async def remove():
            assert worker.name in containers
            containers.remove(worker.name)
            worker._owned = False

        monkeypatch.setattr(worker, "_run", daemon)
        monkeypatch.setattr(worker, "_start", worker._create)
        monkeypatch.setattr(worker, "_remove", remove)
        task = asyncio.create_task(worker.__aenter__())
        await accepted.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        complete.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not containers

    asyncio.run(scenario())


def test_failed_removal_still_closes_rpc_and_refuses_new_commands(tmp_path, monkeypatch):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        closed = []

        class Rpc:
            async def aclose(self):
                closed.append(True)

            async def call(self, *_args):
                return {"exitCode": 0}

        async def remove():
            raise RuntimeError("daemon unavailable")

        worker._owned = True
        worker._rpc = Rpc()
        monkeypatch.setattr(worker, "_remove", remove)
        with pytest.raises(RuntimeError):
            await worker.aclose()
        assert closed == [True]
        with pytest.raises(RuntimeError):
            await worker.execute(["true"])
        worker._owned = False
        await worker.aclose()

    asyncio.run(scenario())


def test_uncertain_creation_is_not_declared_removed_until_observed(tmp_path, monkeypatch):
    from kairos_runtime.experimental.docker_worker import DockerWorker

    async def scenario():
        worker = DockerWorker(tmp_path, image="kairos:external-sandbox")
        worker._owned = True
        worker._creation_uncertain = True
        appeared = False

        async def daemon(*args, **_kwargs):
            if args[0] == "inspect":
                if not appeared:
                    return 1, b"", b"No such object"
                return 0, b'[{"Config":{"Labels":{"io.kairos.external-sandbox":"prototype"}}}]', b""
            return 0, b"", b""

        monkeypatch.setattr(worker, "_run", daemon)
        with pytest.raises(RuntimeError, match="ownership retido"):
            await worker.aclose()
        assert worker._owned
        assert os.path.isdir(worker._config.name)
        appeared = True
        await worker.aclose()
        assert not worker._owned
        assert not os.path.exists(worker._config.name)

    asyncio.run(scenario())


def test_snapshot_copies_content_without_git_or_environment(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/app.py").write_text("print(42)")
    (tmp_path / ".git").write_text("gitdir: /host/private")
    (tmp_path / ".env").write_text("SECRET=private")
    archive = snapshot_project(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        assert tar.getnames() == ["src", "src/app.py"]
        assert tar.extractfile("src/app.py").read() == b"print(42)"
        assert all(m.uid == 0 and m.gid == 0 for m in tar)


@pytest.mark.parametrize("kind", ["file-link", "dir-link", "hardlink", "fifo"])
def test_snapshot_rejects_links_and_special_files(tmp_path, kind):
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("host secret")
    target = project / "escape"
    if kind == "file-link":
        target.symlink_to(outside)
    elif kind == "dir-link":
        target.symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        os.link(outside, target)
    else:
        os.mkfifo(target)
    with pytest.raises(ValueError):
        snapshot_project(project)


def test_snapshot_rejects_symlink_root_and_limits(tmp_path):
    (tmp_path / "file").write_bytes(b"12345")
    with pytest.raises(ValueError):
        snapshot_project(tmp_path, max_bytes=4)
    with pytest.raises(ValueError):
        snapshot_project(tmp_path, max_entries=0)
    link = tmp_path / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError):
        snapshot_project(link)


def policy():
    return WorkerPolicy("sha256:" + "a" * 64, "kairos-worker-" + "b" * 32, writable=False)


def inspection(p):
    return {
        "Image": p.image_id,
        "Name": "/" + p.name,
        "AppArmorProfile": "docker-default",
        "State": {"Running": True},
        "Mounts": [],
        "Config": {
            "User": "10000:10000",
            "WorkingDir": "/workspace",
            "Entrypoint": ["/bin/sleep"],
            "Cmd": ["infinity"],
            "Env": [
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/home/worker",
                "CODEX_HOME=/home/worker/.codex",
            ],
            "Volumes": None,
            "ExposedPorts": None,
            "Labels": {"io.kairos.external-sandbox": "prototype"},
        },
        "HostConfig": {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "CapDrop": ["ALL"],
            "CapAdd": None,
            "SecurityOpt": ["no-new-privileges"],
            "Init": True,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "Devices": [],
            "DeviceRequests": None,
            "DeviceCgroupRules": None,
            "Binds": None,
            "Mounts": None,
            "VolumesFrom": None,
            "PortBindings": {},
            "ExtraHosts": None,
            "Links": None,
            "Sysctls": None,
            "Memory": 512 * 1024**2,
            "MemorySwap": 512 * 1024**2,
            "NanoCpus": 1000000000,
            "PidsLimit": 128,
            "ShmSize": 16 * 1024**2,
            "Tmpfs": p.tmpfs,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "LogConfig": {"Type": "none", "Config": {}},
        },
        "NetworkSettings": {"Networks": {"none": {}}},
    }


def test_worker_policy_accepts_only_the_expected_boundary():
    p = policy()
    p.validate(inspection(p))
    argv = p.create_args()
    assert argv[0] == "create"
    assert "--privileged" not in argv
    assert "--mount" not in argv and "--volume" not in argv
    assert argv[-2:] == [p.image_id, "infinity"]


@pytest.mark.parametrize(
    ("section", "key", "bad"),
    [
        ("HostConfig", "Privileged", True),
        ("HostConfig", "ReadonlyRootfs", False),
        ("HostConfig", "NetworkMode", "bridge"),
        ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
        ("HostConfig", "CapDrop", []),
        ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
        ("HostConfig", "PidMode", "host"),
        ("HostConfig", "IpcMode", "host"),
        ("HostConfig", "Binds", ["/var/run/docker.sock:/var/run/docker.sock"]),
        ("HostConfig", "Memory", 0),
        ("HostConfig", "MemorySwap", -1),
        ("HostConfig", "PidsLimit", 0),
        ("HostConfig", "Tmpfs", {}),
        ("Config", "User", "0"),
        ("Config", "Env", ["HTTPS_PROXY=http://user:secret@proxy"]),
        ("Config", "Volumes", {"/opt/data": {}}),
        ("Config", "Entrypoint", ["/bin/sh"]),
        ("State", "Running", False),
    ],
)
def test_worker_policy_rejects_boundary_changes(section, key, bad):
    p = policy()
    inspected = copy.deepcopy(inspection(p))
    inspected[section][key] = bad
    with pytest.raises(ValueError):
        p.validate(inspected)


@pytest.mark.parametrize(
    ("key", "bad"),
    [
        ("Image", "sha256:" + "c" * 64),
        ("Mounts", [{"Type": "volume", "Destination": "/opt/data"}]),
        ("AppArmorProfile", "unconfined"),
    ],
)
def test_worker_policy_rejects_wrong_image_mount_or_apparmor(key, bad):
    p = policy()
    inspected = inspection(p)
    inspected[key] = bad
    with pytest.raises(ValueError):
        p.validate(inspected)
