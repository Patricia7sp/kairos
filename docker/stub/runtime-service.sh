#!/bin/bash
# Stub do serviço runtime: ecoa o UID com que roda e dorme. Com
# KAIROS_RUNTIME_EXTERNAL=1 o serviço sobe "mudo" — o teste verifica que o
# eco de RUNTIME desaparece, exatamente como o runtime real cede a posse ao
# broker externo.
set -euo pipefail

if [[ "${KAIROS_RUNTIME_EXTERNAL:-0}" == "1" ]]; then
    exec /bin/sleep infinity
fi

echo "RUNTIME uid=$(id -u)"
exec /bin/sleep infinity