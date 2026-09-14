#!/bin/bash
# Wrapper do CMD — versão stub do docker/main-wrapper.sh.
#
# Semelhante ao real: reidratação via with-contenv, HOME/KAIROS_HOME por
# padrão e queda de privilégio para o usuário kairos. A diferença é o
# veredito sintético usado pelos testes de ciclo de vida:
#   boom → exit 42 (o container precisa HERDAR o exit code do main program;
#          o valor 42 é o chamado nos testes).
# Qualquer outro argv cai no kairos-fantasma.
set -euo pipefail

if [ -z "${KAIROS_MAIN_WRAPPER_ENV_READY:-}" ] && [ -x /command/with-contenv ]; then
  export KAIROS_MAIN_WRAPPER_ENV_READY=1
  exec /command/with-contenv "$0" "$@"
fi

export HOME="${HOME:-/opt/data}"
export KAIROS_HOME="${KAIROS_HOME:-/opt/data}"
KAIROS_USER="${KAIROS_USER:-kairos}"

if [ "$#" -gt 0 ] && [ "$1" = "boom" ]; then
  exit 42
fi

if [ "$#" -eq 0 ]; then
  set -- gateway
fi

if [ "$(id -u)" -eq 0 ] && command -v s6-setuidgid >/dev/null 2>&1; then
  exec s6-setuidgid "$KAIROS_USER" /opt/kairos/.venv/bin/kairos "$@"
fi

exec /opt/kairos/.venv/bin/kairos "$@"