#!/usr/bin/env bash
# sg may retain a parent process. Hand systemd the PID that will exec the broker
# so KillMode=mixed delivers SIGINT to the broker on both sg implementations.
set -euo pipefail
broker="$(dirname -- "$0")/../.venv/bin/kairos"
test -x "$broker"
/usr/bin/systemd-notify --pid="$$" --ready
exec "$broker" runtime serve
