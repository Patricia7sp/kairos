"""Amostras do host e cgroups; ausências são explícitas, picos não são inferidos da série."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def lifecycle_barrier(path, timeout=45):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
        ):
            raise ValueError("barreira de recursos inválida")
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("worker_lifecycle_barrier_timeout") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _counters(path):
    rows = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        if len(fields) == 2:
            rows[fields[0]] = int(fields[1])
    return rows


def cgroup(pid):
    try:
        root = Path("/sys/fs/cgroup")
        relative = next(
            line.split(":", 2)[2]
            for line in Path(f"/proc/{int(pid)}/cgroup").read_text().splitlines()
            if line.startswith("0::")
        )
        directory = (root / relative.lstrip("/")).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("cgroup inválido")
        result = {
            "state": "observed",
            "memory_events": _counters(directory / "memory.events"),
            "cpu_stat": _counters(directory / "cpu.stat"),
        }
        for name in ("memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak"):
            path = directory / name
            result[name] = int(path.read_text()) if path.exists() else None
        result["peak_scope"] = "container_lifetime_not_run_peak"
        return result
    except (OSError, ValueError, StopIteration):
        return {"state": "unavailable"}


def snapshot(containers, directory):
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            memory[key] = int(value.split()[0]) * 1024
    disk = shutil.disk_usage(directory)
    result = {
        "at": time.time(),
        "host": {
            "memory_bytes": memory,
            "cpu_ticks": [
                int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]
            ],
            "disk_total_bytes": disk.total,
            "disk_free_bytes": disk.free,
        },
        "containers": {},
    }
    for name in containers:
        if (
            name not in {"kairos", "kairos-runtime-broker"}
            and re.fullmatch(r"[a-f0-9]{64}", name) is None
        ):
            raise ValueError("identidade de container inválida")
        executable = shutil.which("docker")
        if executable is None:
            raise RuntimeError("docker indisponível")
        process = subprocess.run(  # noqa: S603 - inspect fixo; nome/ID validado, sem shell.
            [executable, "inspect", name], capture_output=True, text=True, timeout=15, check=False
        )
        if process.returncode:
            result["containers"][name] = {"state": "unavailable"}
            continue
        row = json.loads(process.stdout)[0]
        result["containers"][name] = {
            "image": row["Image"],
            "running": row["State"]["Running"],
            "oom_killed": row["State"]["OOMKilled"],
            "restarts": row["RestartCount"],
            "cgroup": cgroup(row["State"]["Pid"]),
        }
    return result


def summarize(series):
    gaps = []
    oom = []
    lifecycle = []
    if not series:
        return {"state": "unavailable", "gaps": ["no_resource_samples"], "oom": []}
    for row in series:
        for name, values in row["containers"].items():
            if (
                values.get("state") == "unavailable"
                or values.get("cgroup", {}).get("state") != "observed"
            ):
                gaps.append(name)
            if values.get("oom_killed"):
                oom.append(name)
    first, last = series[0]["containers"], series[-1]["containers"]
    for name in first.keys() & last.keys():
        if (
            first[name].get("restarts") != last[name].get("restarts")
            or first[name].get("image") != last[name].get("image")
            or last[name].get("running") is not True
        ):
            lifecycle.append(name)
        before = first[name].get("cgroup", {}).get("memory_events", {})
        after = last[name].get("cgroup", {}).get("memory_events", {})
        if any(
            after.get(key, 0) > before.get(key, 0) for key in ("oom", "oom_kill", "oom_group_kill")
        ):
            oom.append(name)
    return {
        "state": "failed" if oom or lifecycle else "incomplete" if gaps else "observed",
        "gaps": sorted(set(gaps)),
        "oom": sorted(set(oom)),
        "lifecycle_changes": sorted(set(lifecycle)),
        "sample_count": len(series),
        "series": series,
        "method": "host_proc_and_cgroup_v2_2s_sampling_with_lifetime_peaks",
        "vm": "not_applicable_native_linux_executor",
        "limits": "sampling_does_not_prove_absence_of_transient_processes_or_host_oom",
    }
