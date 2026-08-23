#!/bin/bash
# Wrapper do CMD. Roda como "main program" do /init, herdando stdin, stdout e
# stderr do container — é isso que mantém `--tui` interativo.
#
# /init LIMPA o ambiente antes de invocar o main program, então aqui é preciso
# rehidratar via with-contenv antes de tocar KAIROS_HOME ou PATH. No caminho
# não-PID-1 o env do Dockerfile continua intacto e a etapa é pulada — daí a
# sentinela.
set -euo pipefail

if [ -z "${KAIROS_MAIN_WRAPPER_ENV_READY:-}" ] && [ -x /command/with-contenv ]; then
  export KAIROS_MAIN_WRAPPER_ENV_READY=1
  exec /command/with-contenv "$0" "$@"
fi

export HOME="${HOME:-/opt/data}"
export KAIROS_HOME="${KAIROS_HOME:-/opt/data}"

KAIROS_USER="${KAIROS_USER:-kairos}"

# Sem argumentos: o container existe para servir, então o default é o gateway.
if [ "$#" -eq 0 ]; then
  set -- gateway
fi

# Queda de privilégio do main program. Cada serviço supervisionado faz a sua
# no próprio `run`; esta é a deste caminho.
if [ "$(id -u)" -eq 0 ] && command -v s6-setuidgid >/dev/null 2>&1; then
  exec s6-setuidgid "$KAIROS_USER" /opt/kairos/.venv/bin/kairos "$@"
fi

exec /opt/kairos/.venv/bin/kairos "$@"
