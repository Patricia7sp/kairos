"""Política fixa do protótipo: fail closed diante de divergência do Docker."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIB = 1024**2
LABEL = "io.kairos.external-sandbox"
ENVIRONMENT = {"HOME": "/home/worker", "CODEX_HOME": "/home/worker/.codex"}
IMAGE_ENV_KEYS = frozenset(
    {
        "PATH",
        "LANG",
        "GPG_KEY",  # fingerprint público herdado da imagem oficial Python
        "PYTHON_VERSION",
        "PYTHON_SHA256",
        "PYTHONDONTWRITEBYTECODE",
        "PYTHONUNBUFFERED",
        *ENVIRONMENT,
    }
)


@dataclass(frozen=True)
class WorkerPolicy:
    image_id: str
    name: str
    writable: bool = False

    def __post_init__(self):
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id):
            raise ValueError("imagem deve ser resolvida para ID imutável")
        if not re.fullmatch(r"kairos-worker-[0-9a-f]{32}", self.name):
            raise ValueError("nome de worker inválido")
        if type(self.writable) is not bool:
            raise ValueError("modo inválido")

    @property
    def tmpfs(self) -> dict[str, str]:
        owner = "uid=10000,gid=10000,mode=0700" if self.writable else "uid=0,gid=0,mode=0755"
        return {
            "/workspace": f"rw,nosuid,nodev,size=128m,{owner}",
            "/home/worker": "rw,nosuid,nodev,size=64m,uid=10000,gid=10000,mode=0700",
            "/tmp": "rw,nosuid,nodev,noexec,size=64m,mode=1777",  # noqa: S108 - mount interno
        }

    def create_args(self) -> list[str]:
        args = [
            "create",
            "--name",
            self.name,
            "--label",
            f"{LABEL}=prototype",
            "--hostname",
            "worker",
            "--network",
            "none",
            "--read-only",
            "--user",
            "10000:10000",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--init",
            "--ipc",
            "private",
            "--cgroupns",
            "private",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--shm-size",
            "16m",
            "--restart",
            "no",
            "--log-driver",
            "none",
            "--stop-timeout",
            "2",
            "--workdir",
            "/workspace",
            "--entrypoint",
            "/bin/sleep",
        ]
        for key, value in ENVIRONMENT.items():
            args.extend(["--env", f"{key}={value}"])
        for path, options in self.tmpfs.items():
            args.extend(["--tmpfs", f"{path}:{options}"])
        return [*args, self.image_id, "infinity"]

    def validate(self, inspected: dict[str, Any]) -> None:
        """Attest the local daemon's result before opting out of inner sandbox."""

        def require(condition):
            if not condition:
                raise ValueError("fronteira Docker divergente; worker recusado")

        require(inspected.get("Image") == self.image_id)
        require(inspected.get("Name") == "/" + self.name)
        require(inspected.get("AppArmorProfile") == "docker-default")
        require(inspected.get("State", {}).get("Running") is True)
        require(inspected.get("Mounts") == [])
        config = inspected.get("Config", {})
        for key, expected in {
            "User": "10000:10000",
            "WorkingDir": "/workspace",
            "Entrypoint": ["/bin/sleep"],
            "Cmd": ["infinity"],
        }.items():
            require(config.get(key) == expected)
        require(config.get("Labels", {}).get(LABEL) == "prototype")
        require(not config.get("Volumes") and not config.get("ExposedPorts"))
        environment = {}
        for entry in config.get("Env", []):
            key, sep, value = entry.partition("=")
            require(sep and key in IMAGE_ENV_KEYS and key not in environment)
            environment[key] = value
        require(all(environment.get(key) == value for key, value in ENVIRONMENT.items()))
        host = inspected.get("HostConfig", {})
        for key, expected in {
            "Privileged": False,
            "ReadonlyRootfs": True,
            "NetworkMode": "none",
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Init": True,
            "PidMode": "",
            "IpcMode": "private",
            "UTSMode": "",
            "UsernsMode": "",
            "CgroupnsMode": "private",
            "Memory": 512 * MIB,
            "MemorySwap": 512 * MIB,
            "NanoCpus": 1000000000,
            "PidsLimit": 128,
            "ShmSize": 16 * MIB,
            "Tmpfs": self.tmpfs,
            "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
            "LogConfig": {"Type": "none", "Config": {}},
        }.items():
            require(host.get(key) == expected)
        for key in (
            "CapAdd",
            "Binds",
            "Mounts",
            "VolumesFrom",
            "Devices",
            "DeviceRequests",
            "DeviceCgroupRules",
            "PortBindings",
            "ExtraHosts",
            "Links",
            "Sysctls",
        ):
            require(not host.get(key))
        require(set(inspected.get("NetworkSettings", {}).get("Networks", {})) == {"none"})
