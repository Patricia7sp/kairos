"""Operação temporária E0.3 no host; sem credenciais ou API Docker no servidor QA."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import http.client
import ipaddress
import json
import os
import re
import secrets
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from .measurements import SCENARIOS, ControlSample, summarize_samples
from .seeder import _PROVIDER_KEYS

LABEL = "io.kairos.e03.run_id"
REPO = Path(__file__).resolve().parents[2]


def command(*args, body=None, timeout=60):
    if not args or args[0] not in {"docker", "git", "tailscale"}:
        raise ValueError("temporary_executable_refused")
    executable = shutil.which(args[0])
    if executable is None:
        raise RuntimeError("temporary_executable_unavailable")
    result = subprocess.run(  # noqa: S603 - executável allowlisted; argv estruturado, sem shell.
        [executable, *args[1:]],
        input=body,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("temporary_command_failed:" + args[0])
    return result.stdout.strip()


def write_json(path, value, mode=0o644):
    temporary = path.with_suffix(".new")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=True, allow_nan=False))
    temporary.chmod(mode)
    temporary.replace(path)


@dataclasses.dataclass(frozen=True)
class LaunchConfig:
    image: str
    worker_image: str
    directory: Path
    ttl: int = 3300

    def validate(self):
        if any(
            re.fullmatch(r"sha256:[a-f0-9]{64}", value) is None
            for value in (self.image, self.worker_image)
        ):
            raise ValueError("imagem imutável obrigatória")
        path = self.directory.absolute()
        if (
            path != path.resolve()
            or path.exists()
            or not path.parent.is_dir()
            or not 60 <= self.ttl <= 3300
        ):
            raise ValueError("diretório novo privado e TTL limitado obrigatórios")
        if any(os.environ.get(key) for key in _PROVIDER_KEYS):
            raise ValueError("credencial herdada recusada")


def require_owned(inspected, object_id, run_id, network=False):
    labels = (
        inspected.get("Labels", {}) if network else inspected.get("Config", {}).get("Labels", {})
    )
    if inspected.get("Id") != object_id or labels.get(LABEL) != run_id:
        raise ValueError("identidade divergente; recurso preservado")


def validate_mounts(mounts, directory):
    expected = {
        "/opt/kairos/scripts/e03": str(directory / "code"),
        "/opt/kairos/kairos_web/ui/e03": str(directory / "code/ui"),
        "/opt/kairos/kairos_web/ui/e03-run": str(directory / "public"),
    }
    if len(mounts) != len(expected) or {row["Destination"] for row in mounts} != set(expected):
        raise ValueError("temporary_mount_mismatch")
    for row in mounts:
        if (
            row["Type"] != "bind"
            or row["RW"] is not False
            or row["Source"] != expected[row["Destination"]]
        ):
            raise ValueError("temporary_mount_mismatch")


def _client_metadata(value):
    allowed = {
        "user_agent",
        "platform",
        "protocol",
        "secure_context",
        "origin",
        "clock",
        "capture",
        "includes_network",
    }
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("metadados inválidos")
    output = {}
    for key, item in value.items():
        if key in {"secure_context", "includes_network"}:
            if type(item) is not bool:
                raise ValueError("metadados inválidos")
        elif not isinstance(item, str) or len(item) > 512 or any(ord(c) < 32 for c in item):
            raise ValueError("metadados inválidos")
        output[key] = item
    return output


def validate_report(raw, run_id, receipts, exposures):
    if (
        not isinstance(raw, dict)
        or set(raw) != {"schema_version", "run_id", "client", "samples"}
        or raw["schema_version"] != 1
        or raw["run_id"] != run_id
    ):
        raise ValueError("relatório inválido")
    if not isinstance(raw["samples"], list) or len(raw["samples"]) > 1000:
        raise ValueError("amostras inválidas")
    samples = [ControlSample(**row) for row in raw["samples"]]
    summary = summarize_samples(samples)
    missing = []
    for row in summary["samples"]:
        if row["outcome"] != "denied":
            continue
        receipt = receipts.get((row["session_id"], row["call_id"]))
        intervals = exposures.get(row["scenario"], [])
        if (
            not receipt
            or receipt["id"] != row["persisted_message_id"]
            or receipt.get("status") != "denied"
        ):
            missing.append({"call_id": row["call_id"], "reason": "server_receipt_missing"})
        elif not any(
            span.get("passed") is True and span["start"] <= receipt["timestamp"] <= span["end"]
            for span in intervals
        ):
            missing.append(
                {"call_id": row["call_id"], "reason": "successful_workload_not_observed"}
            )
    exposure_complete = all(exposures.get(case) for case in SCENARIOS) and not missing
    if not exposure_complete:
        summary["gate"] = "inconclusive"
    summary.update(
        {
            "run_id": run_id,
            "client": _client_metadata(raw["client"]),
            "workload_state": "observed" if exposure_complete else "incomplete",
            "workload_gaps": missing,
            "workload_cycles": exposures,
        }
    )
    return summary


class _Forward(socketserver.BaseRequestHandler):
    def handle(self):
        import select

        with socket.create_connection(self.server.target, timeout=10) as upstream:
            self.request.settimeout(10)
            upstream.settimeout(10)
            peers = [self.request, upstream]
            while not self.server.stopping.is_set():
                ready, _, _ = select.select(peers, [], [], 1)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)


class LoopbackForward(socketserver.ThreadingTCPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, port=9119):
        target = ipaddress.ip_address(address)
        if (
            target.version != 4
            or not target.is_private
            or target.is_unspecified
            or target.is_multicast
        ):
            raise ValueError("destino da ponte inválido")
        self.target = (str(target), port)
        self.stopping = threading.Event()
        self.failures = 0
        self.rejected = 0
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(("127.0.0.1", 0), _Forward)
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.rejected += 1
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        self.failures += 1

    def close(self):
        self.stopping.set()
        self.shutdown()
        self.server_close()
        self.thread.join(timeout=5)


class TemporaryRun:
    def __init__(self, config):
        config.validate()
        self.config = config
        self.run_id = uuid.uuid4().hex
        self.name = "kairos-e03-" + self.run_id[:12]
        self.directory = config.directory.absolute()
        self.public = self.directory / "public"
        self.container = self.network = self.forward = None
        self.token = secrets.token_urlsafe(48)
        self.deadline = time.time() + config.ttl
        self.workload = None
        self.workload_log = None
        self.active = None
        self.intervals = {case: [] for case in SCENARIOS}
        self.stats = []
        self.last_stats = 0
        self.tailscale_before = None
        self.funnel_before = None
        self.served = False
        self.final_report = None

    def prepare(self):
        self.directory.mkdir(mode=0o700)
        info = self.directory.stat()
        self.directory_identity = (info.st_dev, info.st_ino)
        self.public.mkdir(mode=0o755)
        (self.directory / "workloads").mkdir(mode=0o700)
        secret = self.directory / "credential"
        secret.write_text(self.token)
        secret.chmod(0o600)
        snapshot = self.directory / "code"
        shutil.copytree(
            REPO / "scripts/e03", snapshot, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
        for path in snapshot.rglob("*"):
            path.chmod(0o755 if path.is_dir() else 0o644)
        snapshot.chmod(0o755)
        image_info = json.loads(command("docker", "image", "inspect", self.config.image))[0]
        if set(image_info["Config"].get("Volumes") or {}) - {"/opt/data"}:
            raise ValueError("unexpected_image_volume")
        self.network = command(
            "docker",
            "network",
            "create",
            "--internal",
            "--label",
            f"{LABEL}={self.run_id}",
            self.name,
        )
        self.container = command(
            "docker",
            "run",
            "-d",
            "--name",
            self.name,
            "--label",
            f"{LABEL}={self.run_id}",
            "--network",
            self.network,
            "--user",
            "10000:10000",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--memory",
            "512m",
            "--memory-swap",
            "512m",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--no-healthcheck",
            "--restart",
            "no",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=64m,mode=1777",  # noqa: S108 - tmpfs privado no namespace do container.
            "--tmpfs",
            "/e03:rw,noexec,nosuid,nodev,size=256m,uid=10000,gid=10000,mode=0700",
            "--tmpfs",
            "/opt/data:rw,noexec,nosuid,nodev,size=8m,uid=10000,gid=10000,mode=0700",
            "--mount",
            f"type=bind,src={snapshot},dst=/opt/kairos/scripts/e03,readonly",
            "--mount",
            f"type=bind,src={snapshot / 'ui'},dst=/opt/kairos/kairos_web/ui/e03,readonly",
            "--mount",
            f"type=bind,src={self.public},dst=/opt/kairos/kairos_web/ui/e03-run,readonly",
            "--env",
            "KAIROS_E03_TEMPORARY=1",
            "--env",
            "KAIROS_HOME=/e03",
            "--env",
            "HOME=/e03",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--workdir",
            "/opt/kairos",
            "--entrypoint",
            "/opt/kairos/.venv/bin/python",
            self.config.image,
            "-m",
            "scripts.e03.serve",
            "--host",
            "container",
        )
        bootstrap = "import json,pathlib,sys;d=json.load(sys.stdin);p=pathlib.Path('/e03');(p/'config.yaml').write_text('{}\\n');(p/'web-token').write_text(d.pop('token'));(p/'web-token').chmod(0o600);(p/'.e03-temporary').write_text(json.dumps(d));(p/'.e03-temporary').chmod(0o600)"
        command(
            "docker",
            "exec",
            "-i",
            self.container,
            "/opt/kairos/.venv/bin/python",
            "-c",
            bootstrap,
            body=json.dumps(
                {
                    "token": self.token,
                    "profile": "e03-synthetic",
                    "run_id": self.run_id,
                    "expires_at": self.deadline,
                }
            ),
        )
        inspected = self.inspect()
        self.attest(inspected)
        probe = "import json,os,pathlib;rows=dict(line.split(':',1) for line in pathlib.Path('/proc/self/status').read_text().splitlines() if ':' in line);print(json.dumps({'uid':os.getuid(),**{key:rows[key].strip() for key in ('CapEff','CapBnd','NoNewPrivs','Seccomp')}}))"
        kernel = json.loads(
            command(
                "docker",
                "exec",
                "--user",
                "10000:10000",
                self.container,
                "/opt/kairos/.venv/bin/python",
                "-I",
                "-B",
                "-c",
                probe,
            )
        )
        if kernel != {
            "uid": 10000,
            "CapEff": "0000000000000000",
            "CapBnd": "0000000000000000",
            "NoNewPrivs": "1",
            "Seccomp": "2",
        }:
            raise ValueError("temporary_kernel_policy_mismatch")
        self.kernel_attestation = kernel
        address = next(iter(inspected["NetworkSettings"]["Networks"].values()))["IPAddress"]
        self.forward = LoopbackForward(address)
        self.local_origin = f"http://127.0.0.1:{self.forward.server_address[1]}"
        self.origin = self.local_origin
        write_json(
            self.public / "manifest.json",
            {"profile": "e03-synthetic", "run_id": self.run_id, "origin": self.origin},
        )
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                value = self.api("/api/auth/me")
                if value.get("authenticated") is not True:
                    raise ValueError("temporary_auth_failed")
                break
            except (OSError, ValueError):
                time.sleep(0.1)
        else:
            raise RuntimeError("temporary_health_timeout")
        self.status("waiting")
        write_json(
            self.directory / "identity.json",
            {
                "run_id": self.run_id,
                "container": self.container,
                "network": self.network,
                "expires_at": self.deadline,
            },
            0o600,
        )

    def inspect(self):
        row = json.loads(command("docker", "inspect", self.container))[0]
        require_owned(row, self.container, self.run_id)
        return row

    def attest(self, row):
        host = row["HostConfig"]
        expected = {
            "ReadonlyRootfs": True,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges"],
            "Memory": 512 * 1024**2,
            "MemorySwap": 512 * 1024**2,
            "NanoCpus": 10**9,
            "PidsLimit": 128,
        }
        if (
            row["Image"] != self.config.image
            or row["Config"]["User"] != "10000:10000"
            or not row["State"]["Running"]
            or any(host.get(key) != value for key, value in expected.items())
        ):
            raise ValueError("temporary_container_policy_mismatch")
        if host.get("PortBindings") or row.get("AppArmorProfile") != "docker-default":
            raise ValueError("temporary_port_or_apparmor_mismatch")
        validate_mounts(row["Mounts"], self.directory)
        if set(host["Tmpfs"]) != {"/tmp", "/e03", "/opt/data"}:  # noqa: S108 - destinos de tmpfs no namespace, não arquivos do host.
            raise ValueError("temporary_tmpfs_mismatch")
        networks = row["NetworkSettings"]["Networks"]
        if len(networks) != 1 or next(iter(networks.values()))["NetworkID"] != self.network:
            raise ValueError("temporary_network_mismatch")
        net = json.loads(command("docker", "network", "inspect", self.network))[0]
        require_owned(net, self.network, self.run_id, network=True)
        if net["Internal"] is not True:
            raise ValueError("temporary_egress_mismatch")

    def api(self, path):
        if not path.startswith("/api/") or path.startswith("//") or any(ord(c) < 32 for c in path):
            raise ValueError("temporary_api_path_invalid")
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.forward.server_address[1], timeout=10
        )
        try:
            connection.request("GET", path, headers={"X-Kairos-Session-Token": self.token})
            response = connection.getresponse()
            if response.status != 200:
                raise RuntimeError("temporary_api_failed")
            return json.loads(response.read(2**22))
        finally:
            connection.close()

    def status(self, state):
        write_json(
            self.public / "status.json",
            {"run_id": self.run_id, "state": state, "active_scenario": self.active},
        )

    def publish(self):
        self.tailscale_before = json.loads(command("tailscale", "serve", "status", "--json"))
        self.funnel_before = json.loads(command("tailscale", "funnel", "status", "--json"))
        tail = json.loads(command("tailscale", "status", "--json"))
        hostname = tail["Self"]["DNSName"].rstrip(".")
        if not re.fullmatch(r"[a-z0-9.-]+\.ts\.net", hostname):
            raise ValueError("tailnet_hostname_invalid")
        self.origin = f"https://{hostname}:8443"
        for baseline in (self.tailscale_before, self.funnel_before):
            if (
                "8443" in baseline.get("TCP", {})
                or any(key.endswith(":8443") for key in baseline.get("Web", {}))
                or baseline.get("AllowFunnel", {}).get(f"{hostname}:8443")
            ):
                raise ValueError("temporary_https_port_in_use")
        # Armar cleanup antes do comando: um erro pode ocorrer após aplicar a regra.
        self.served = True
        command("tailscale", "serve", "--bg", "--https=8443", self.local_origin)
        self.attest_tailnet(published=True)
        write_json(
            self.public / "manifest.json",
            {"profile": "e03-synthetic", "run_id": self.run_id, "origin": self.origin},
        )

    def attest_tailnet(self, *, published):
        target = self.origin.removeprefix("https://")
        for service, baseline in (("serve", self.tailscale_before), ("funnel", self.funnel_before)):
            current = json.loads(command("tailscale", service, "status", "--json"))
            expected = copy.deepcopy(baseline)
            if published:
                expected.setdefault("TCP", {})["8443"] = {"HTTPS": True}
                expected.setdefault("Web", {})[target] = {
                    "Handlers": {"/": {"Proxy": self.local_origin}}
                }
            if current.get("AllowFunnel", {}).get(target):
                raise ValueError("temporary_funnel_mismatch")
            # O daemon pode conservar mapas vazios após remover a última regra.
            if {key: value for key, value in current.items() if value != {}} != {
                key: value for key, value in expected.items() if value != {}
            }:
                raise ValueError("temporary_tailnet_config_mismatch")

    def finish_workload(self, timeout=240):
        if self.active in ("ocioso", "conversa_painel"):
            if self.intervals[self.active]:
                self.intervals[self.active][-1]["end"] = time.time()
            self.active = None
        if self.workload:
            (self.directory / "stop-workload").touch(mode=0o600)
            try:
                end = time.monotonic() + timeout
                while self.workload.poll() is None:
                    self.capture_resources()
                    if time.monotonic() >= end:
                        raise subprocess.TimeoutExpired("fixed_workload", timeout)
                    time.sleep(0.1)
                code = self.workload.returncode
            except subprocess.TimeoutExpired:
                os.killpg(self.workload.pid, signal.SIGTERM)
                try:
                    self.workload.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(self.workload.pid, signal.SIGKILL)
                    self.workload.wait(timeout=10)
                raise RuntimeError("workload_shutdown_timeout") from None
            finally:
                self.workload_log.close()
                self.workload_stderr.close()
                self.workload = None
            if code:
                raise RuntimeError("workload_failed")
            self.read_cycles()

    def read_cycles(self):
        if self.active not in ("auditoria", "execucao_isolada", "recuperacao"):
            return
        log = self.directory / f"{self.active}.jsonl"
        if log.exists():
            lines = log.read_text().splitlines()
            rows = [json.loads(line) for line in lines if line.endswith("}")]
            if any(
                row.get("passed") is not True or row.get("scenario") != self.active for row in rows
            ):
                raise RuntimeError("workload_failed")
            self.intervals[self.active] = rows

    def set_scenario(self, scenario):
        if scenario not in SCENARIOS:
            raise ValueError("scenario_invalid")
        self.finish_workload()
        self.active = scenario
        self.status("preparing")
        if scenario in ("ocioso", "conversa_painel"):
            self.intervals[scenario].append(
                {
                    "start": time.time(),
                    "end": self.deadline,
                    "passed": True,
                    "details": {
                        "contract": "browser_synthetic_conversation"
                        if scenario == "conversa_painel"
                        else "no_extra_workload"
                    },
                }
            )
        else:
            (self.directory / "stop-workload").unlink(missing_ok=True)
            self.workload_log = (self.directory / f"{scenario}.jsonl").open("w")
            self.workload_stderr = (self.directory / f"{scenario}.stderr").open("w")
            self.workload = subprocess.Popen(  # noqa: S603 - módulo fixo, cenário enum e imagem SHA validados.
                [
                    sys.executable,
                    "-m",
                    "scripts.e03.workloads",
                    "--scenario",
                    scenario,
                    "--worker-image",
                    self.config.worker_image,
                    "--run-id",
                    self.run_id,
                    "--stop-file",
                    str(self.directory / "stop-workload"),
                ],
                cwd=REPO,
                stdout=self.workload_log,
                stderr=self.workload_stderr,
                start_new_session=True,
                env={
                    **os.environ,
                    "TMPDIR": str(self.directory / "workloads"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "KAIROS_E03_RESOURCE_LOCK": str(self.directory / "resources.lock"),
                },
            )
            end = time.monotonic() + 120
            while time.monotonic() < end:
                self.capture_resources()
                self.read_cycles()
                if self.workload.poll() is not None:
                    raise RuntimeError("workload_ended_early")
                if self.intervals[scenario]:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError("workload_ready_timeout")
        self.status("running")

    def cleanup_workers(self, errors):
        workers = command(
            "docker", "ps", "-aq", "--no-trunc", "--filter", f"label={LABEL}={self.run_id}"
        ).splitlines()
        for worker_id in workers:
            if worker_id == self.container:
                continue
            row = json.loads(command("docker", "inspect", worker_id))[0]
            require_owned(row, worker_id, self.run_id)
            if (
                row["Image"] != self.config.worker_image
                or re.fullmatch(r"/kairos-worker-[a-f0-9]{32}", row["Name"]) is None
            ):
                errors.append("worker_identity_mismatch")
                continue
            command("docker", "rm", "-f", worker_id)

    def cleanup_directory(self):
        if self.directory.exists():
            info = self.directory.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or (info.st_dev, info.st_ino) != self.directory_identity
            ):
                raise ValueError("temporary_directory_identity_mismatch")
            (self.directory / "credential").unlink(missing_ok=True)
            for name in ("code", "public", "workloads"):
                if (self.directory / name).exists():
                    shutil.rmtree(self.directory / name)

    def cleanup(self):
        errors = []
        try:
            self.finish_workload()
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            errors.append("workload_cleanup_failed")
        if self.served:
            try:
                current = json.loads(command("tailscale", "serve", "status", "--json"))
                listener = current.get("Web", {}).get(self.origin.removeprefix("https://"), {})
                if current.get("TCP", {}).get("8443") != {"HTTPS": True} or listener != {
                    "Handlers": {"/": {"Proxy": self.local_origin}}
                }:
                    raise ValueError("temporary_listener_identity_mismatch")
                command("tailscale", "serve", "--https=8443", "off")
                self.attest_tailnet(published=False)
                self.served = False
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                errors.append("listener_cleanup_failed")
        if self.forward:
            self.forward.close()
        try:
            self.cleanup_workers(errors)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            errors.append("worker_cleanup_failed")
        for object_id, network in ((self.container, False), (self.network, True)):
            if not object_id:
                continue
            try:
                args = (
                    ("docker", "network", "inspect", object_id)
                    if network
                    else ("docker", "inspect", object_id)
                )
                row = json.loads(command(*args))[0]
                require_owned(row, object_id, self.run_id, network)
                command(
                    *(
                        ("docker", "network", "rm", object_id)
                        if network
                        else ("docker", "rm", "-f", object_id)
                    )
                )
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                errors.append("network_cleanup_failed" if network else "container_cleanup_failed")
        self.cleanup_directory()
        self.token = ""
        if self.final_report is not None:
            self.final_report["teardown_state"] = "failed" if errors else "complete"
            self.final_report["teardown_errors"] = errors
            write_json(self.directory / "report.json", self.final_report, 0o600)
        if errors:
            raise RuntimeError(",".join(errors))

    def sessions(self):
        rows = self.api("/api/sessions?limit=100&tag=e03-synthetic")["sessions"]
        return [row for row in rows if row["title"].startswith(f"e03-synthetic:{self.run_id}:")]

    def messages(self, session_id):
        if re.fullmatch(r"e03-synthetic-[a-f0-9]{32}", session_id) is None:
            raise ValueError("session_invalid")
        return self.api(f"/api/sessions/{session_id}/messages")["messages"]

    def receipts(self, raw):
        result = {}
        for session_id in {row["session_id"] for row in raw["samples"]}:
            for row in self.messages(session_id):
                if row["role"] != "tool" or not row.get("tool_call_id"):
                    continue
                content = json.loads(row["content"])
                key = (session_id, row["tool_call_id"])
                if key in result:
                    raise ValueError("duplicate_server_receipt")
                result[key] = {
                    "id": row["id"],
                    "timestamp": row["created_at"],
                    "status": content.get("status"),
                }
        return result

    def finalize_report(self, raw):
        from .resources import summarize

        self.finish_workload()
        self.last_stats = 0
        self.capture_resources()
        rows = self.sessions()
        pressure = [row for row in rows if row["title"].endswith(":pressure")]
        if len(pressure) == 1:
            activity = [row for row in self.messages(pressure[0]["id"]) if row["role"] == "user"]
            if len(activity) >= 2 and activity[-1]["content"] == "e03-synthetic:painel-fim":
                self.intervals["conversa_painel"] = [
                    {
                        "start": activity[0]["created_at"],
                        "end": activity[-1]["created_at"],
                        "passed": True,
                        "details": {
                            "contract": "real_secondary_browser_chat_channel",
                            "user_turns": len(activity),
                        },
                    }
                ]
            else:
                self.intervals["conversa_painel"] = []
        else:
            self.intervals["conversa_painel"] = []
        report = validate_report(raw, self.run_id, self.receipts(raw), self.intervals)
        resources = summarize(self.stats)
        report["resource_state"] = resources["state"]
        report["server_kernel_attestation"] = self.kernel_attestation
        report["loopback_forward_errors"] = self.forward.failures
        report["loopback_forward_rejections"] = self.forward.rejected
        report["resources"] = resources
        report["artifacts"] = {
            "source_commit": command("git", "-C", str(REPO), "rev-parse", "HEAD"),
            "application_image": self.config.image,
            "worker_image": self.config.worker_image,
        }
        residuals = command(
            "docker", "ps", "-aq", "--no-trunc", "--filter", f"label={LABEL}={self.run_id}"
        ).splitlines()
        report["residual_workers"] = [value for value in residuals if value != self.container]
        report["teardown_state"] = "pending_until_exit_or_ttl"
        report["formal_e0_acceptance"] = "pending_owner_review_and_client_transport"
        if resources["state"] != "observed" or report["residual_workers"]:
            report["gate"] = "inconclusive"
        self.final_report = report
        write_json(self.public / "report.json", report)
        write_json(self.directory / "report.json", report, 0o600)
        self.status("collected")

    def capture_resources(self):
        from .resources import lifecycle_barrier, snapshot

        if time.monotonic() - self.last_stats < 2:
            return
        with lifecycle_barrier(self.directory / "resources.lock"):
            owned = command(
                "docker", "ps", "-q", "--no-trunc", "--filter", f"label={LABEL}={self.run_id}"
            ).splitlines()
            self.stats.append(snapshot(["kairos", "kairos-runtime-broker", *owned], self.directory))
        self.last_stats = time.monotonic()

    def monitor(self):
        while time.time() < self.deadline:
            self.capture_resources()
            if not self.inspect()["State"]["Running"]:
                raise RuntimeError("temporary_server_stopped")
            if self.workload:
                if self.workload.poll() is not None:
                    raise RuntimeError("workload_ended_early")
                self.read_cycles()
            if self.final_report is None:
                sessions = self.sessions()
                cases = {row["title"].rsplit(":", 1)[1]: row for row in sessions}
                if "report" in cases:
                    content = [
                        row["content"]
                        for row in self.messages(cases["report"]["id"])
                        if row["role"] == "user"
                        and row["content"].startswith("e03-synthetic:report:")
                    ]
                    if len(content) != 1:
                        raise ValueError("report_message_invalid")
                    raw = json.loads(content[0].removeprefix("e03-synthetic:report:"))
                    self.finalize_report(raw)
                else:
                    for scenario in SCENARIOS:
                        if (
                            scenario in cases
                            and scenario != self.active
                            and not self.intervals[scenario]
                        ):
                            self.set_scenario(scenario)
                            break
            time.sleep(0.2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporary", action="store_true")
    parser.add_argument("--image", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--ttl", type=int, default=3300)
    parser.add_argument("--publish-tailnet", action="store_true")
    args = parser.parse_args(argv)
    if not args.temporary:
        print(
            json.dumps({"state": "error", "error_kind": "explicit_temporary_flag_required"}),
            file=sys.stderr,
        )
        return 2
    run = None
    result = 0

    def stop(*_):
        if run is not None:
            run.deadline = time.time()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        if command("git", "-C", str(REPO), "status", "--porcelain"):
            raise ValueError("clean_committed_source_required")
        config = LaunchConfig(args.image, args.worker_image, args.directory, args.ttl)
        run = TemporaryRun(config)
        run.prepare()
        if args.publish_tailnet:
            run.publish()
        print(
            json.dumps(
                {
                    "state": "ready",
                    "scope": "temporary_e03_only",
                    "url": run.origin + "/ui/e03/index.html",
                    "credential_file": str(run.directory / "credential"),
                    "run_id": run.run_id,
                    "expires_at": run.deadline,
                }
            ),
            flush=True,
        )
        run.monitor()
        if run.final_report is None:
            write_json(
                run.directory / "report.json",
                {
                    "run_id": run.run_id,
                    "scope": "temporary_e03_only",
                    "gate": "inconclusive",
                    "reason": "client_measurement_not_received",
                },
                0o600,
            )
            result = 69
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        if run is not None and run.public.exists():
            run.status("failed")
        print(
            json.dumps(
                {"state": "error", "error_kind": type(exc).__name__, "scope": "temporary_e03_only"}
            ),
            file=sys.stderr,
            flush=True,
        )
        result = 1
    finally:
        if run is not None:
            try:
                run.cleanup()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
                print(
                    json.dumps(
                        {"state": "error", "error_kind": type(exc).__name__, "stage": "cleanup"}
                    ),
                    file=sys.stderr,
                )
                result = 1
    return result
