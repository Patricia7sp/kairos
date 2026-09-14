import ctypes
import errno
import json
import os
import platform
from pathlib import Path

assert platform.machine() == "x86_64", "diagnóstico somente amd64"
assert os.geteuid() == 10000, "execute somente como UID 10000 no container descartável"
assert Path("/proc/self/attr/current").read_text().strip() in {
    "kairos-runtime-diagnostic (enforce)",
    "kairos-runtime-candidate (enforce)",
}, "perfil de diagnóstico ausente"
status = Path("/proc/self/status").read_text()
assert "Seccomp:\t2" in status, "seccomp precisa estar ativo"
assert "CapEff:\t0000000000000000" in status, "não executar com capabilities efetivas"

libc = ctypes.CDLL(None, use_errno=True)
cases = {
    "unshare_mount_only": lambda: libc.unshare(0x20000),
    "unshare_user_plus_mount": lambda: libc.unshare(0x10020000),
    "setns": lambda: libc.setns(-1, 0),
    "mount_unrestricted_flags": lambda: libc.mount(None, b"/", None, 0, None),
    "remount_without_nosuid": lambda: libc.mount(None, b"/", None, 0x9020, None),
    "umount_force": lambda: libc.umount2(b"/", 1),
    "bpf": lambda: libc.syscall(321, 0, 0, 0),
}
results = {}
for name, call in cases.items():
    ctypes.set_errno(0)
    result = call()
    code = ctypes.get_errno()
    results[name] = {"result": result, "errno": code}
    assert result == -1 and code == errno.EPERM, (name, result, code)
print(json.dumps(results))
