"""Check mount rule expansion without loading policy into the kernel.

This catches AARE root-directory and flag-normalization mistakes. It checks
parser expressions before final DFA enforcement; it is not an isolation test.
Run with the same apparmor_parser version used to load the profile.
"""

# These /tmp strings describe bwrap mount requests; no host files are created.
# ruff: noqa: S108

import re
import shutil
import subprocess
from pathlib import Path

profile = Path(__file__).with_name("apparmor-candidate").resolve()
parser = shutil.which("apparmor_parser")
assert parser is not None, "apparmor_parser is required"
# Fixed arguments and repository-owned profile, with no shell invocation.
result = subprocess.run(  # noqa: S603
    [parser, "-Q", "-T", "-D", "rule-exprs", str(profile)],
    capture_output=True,
    text=True,
    timeout=30,
    check=True,
)
patterns = []
for line in (result.stdout + result.stderr).splitlines():
    if line.startswith("rule: ") and "< 0x2>" in line:
        patterns.append(re.compile(line[6:].split("  ->  ", 1)[0]))
assert patterns, "parser produced no mount expressions"


def accepts(target, source, filesystem, flags):
    encoded_flags = "".join(chr(bit + 1) for bit in range(32) if flags & (1 << bit))
    request = "\x07" + target + "\x00" + source + "\x00" + filesystem + "\x00" + encoded_flags
    return any(pattern.fullmatch(request) for pattern in patterns)


allowed = [
    ("/", "", "", 0x8C000),
    ("/tmp/", "tmpfs", "tmpfs", 6),
    ("/tmp/newroot/", "/tmp/newroot/", "", 0x5000),
    ("/newroot/", "tmpfs", "tmpfs", 6),
    ("/newroot/", "/oldroot/", "", 0x5000),
    ("/newroot/dev/", "tmpfs", "tmpfs", 6),
    ("/newroot/dev/null", "/oldroot/dev/null", "", 0x5000),
    ("/newroot/dev/pts/", "devpts", "devpts", 10),
    ("/newroot/proc/", "proc", "proc", 14),
    ("/oldroot/", "", "", 0x4C000),
]
optional_flags = [1, 4, 8, 0x400, 0x800, 0x200000]
for combination in range(64):
    flags = 0x9022
    for bit, flag in enumerate(optional_flags):
        if combination & (1 << bit):
            flags |= flag
    allowed.append(("/newroot/", "", "", flags))
    allowed.append(("/newroot/projects/example/", "", "", flags))
for request in allowed:
    assert accepts(*request), ("expected allow", request)

denied = [
    ("/etc/", "tmpfs", "tmpfs", 6),
    ("/newroot/", "overlay", "overlay", 6),
    ("/newroot/", "/etc/", "", 0x5000),
    ("/etc/", "/oldroot/", "", 0x5000),
    ("/newroot/", "", "", 0x9020),
    ("/newroot/", "proc", "proc", 14),
    ("/newroot/", "devpts", "devpts", 10),
]
for request in denied:
    assert not accepts(*request), ("expected deny", request)
print(f"Mount expressions: {len(allowed)} allowed cases and {len(denied)} denied cases passed")
