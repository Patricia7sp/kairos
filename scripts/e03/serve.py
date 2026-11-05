"""Bootstrap dedicado: o servidor normal, com gateway sintético no processo temporário."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import socket
import stat
import sys
import time
from pathlib import Path


def _error(kind):
    sys.stderr.write(
        json.dumps({"state": "error", "scope": "e03_temporary", "error_kind": kind}) + "\n"
    )
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9119)
    parser.add_argument("--wait-profile-seconds", type=float, default=20)
    args = parser.parse_args(argv)
    if os.environ.get("KAIROS_E03_TEMPORARY") != "1":
        return _error("temporary_profile_required")
    supplied = os.environ.get("KAIROS_HOME")
    if not supplied:
        return _error("temporary_home_invalid")
    home = Path(supplied).absolute()
    if home == Path("/opt/data") or home != home.resolve():
        return _error("temporary_home_invalid")
    if not 0 <= args.port <= 65535 or not 0 <= args.wait_profile_seconds <= 30:
        return _error("temporary_arguments_invalid")
    try:
        supplied_host = (
            socket.gethostbyname(socket.gethostname()) if args.host == "container" else args.host
        )
        host = ipaddress.ip_address(supplied_host)
        if (
            host.version != 4
            or host.is_unspecified
            or host.is_multicast
            or not (host.is_loopback or host.is_private)
        ):
            return _error("temporary_arguments_invalid")
    except (ValueError, OSError):
        return _error("temporary_arguments_invalid")
    deadline = time.monotonic() + args.wait_profile_seconds
    while not (home / ".e03-temporary").exists():
        if time.monotonic() >= deadline:
            return _error("temporary_profile_missing")
        time.sleep(0.05)
    try:
        from scripts.e03.seeder import install_gateway_seam, require_temporary_profile

        require_temporary_profile(home)
        token = home / "web-token"
        info = token.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
            or info.st_nlink != 1
            or not 32 <= info.st_size <= 1024
        ):
            return _error("temporary_credential_invalid")
        if "kairos_web.server" in sys.modules:
            return _error("fresh_process_required")
        with install_gateway_seam(home):
            import uvicorn

            from kairos_web.server import app

            uvicorn.run(
                app,
                host=str(host),
                port=args.port,
                access_log=False,
                proxy_headers=False,
                log_level="warning",
            )
    except (OSError, ValueError):
        return _error("temporary_profile_invalid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
