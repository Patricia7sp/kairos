#!/bin/bash
# Despacho de entrypoint — o problema do PID 1.
#
# O s6-overlay EXIGE ser o PID 1 e aborta com "can only run as pid 1" quando
# não é. Mas há runtimes legítimos em que ele nunca será: Fly Machines,
# `docker run --init`, e algumas configurações de Nomad/K8s executam o
# entrypoint como filho do init da plataforma.
#
# Em vez de falhar nesses runtimes, o dispatcher escolhe o caminho:
#
#   PID 1      → árvore de supervisão completa do s6
#   não-PID-1  → bootstrap direto + exec do wrapper, SEM supervisão
#
# A degradação é ANUNCIADA. Falhar em silêncio seria pior, mas seguir em
# silêncio também: o operador precisa saber que naquele runtime o dashboard
# não sobe.
set -euo pipefail

if [ "$$" -eq 1 ]; then
  exec /init /opt/kairos/docker/main-wrapper.sh "$@"
fi

echo "kairos: não sou PID 1 (o init da plataforma é); o s6-overlay não pode" >&2
echo "kairos: iniciar. Serviços supervisionados (gateway, dashboard) ficam"  >&2
echo "kairos: INDISPONÍVEIS neste runtime, mas o comando pedido vai rodar."  >&2

# /init normalmente semeia o PATH com os helpers do s6. Este caminho pula o
# /init, então a rehidratação é manual — sem ela, s6-setuidgid não existe e o
# shim de exec falha.
export PATH="/command:/package/admin/s6/command:${PATH}"

/opt/kairos/docker/stage2-hook.sh
exec /opt/kairos/docker/main-wrapper.sh "$@"
