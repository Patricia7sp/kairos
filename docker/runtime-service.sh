#!/bin/bash
set -euo pipefail

# Manter o serviço s6 vivo sem disputar a posse do broker externo.
if [[ "${KAIROS_RUNTIME_EXTERNAL:-0}" == "1" ]]; then
    exec /bin/sleep infinity
fi
exec /opt/kairos/.venv/bin/kairos runtime serve
