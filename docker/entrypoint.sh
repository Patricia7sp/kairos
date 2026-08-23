#!/bin/bash
# DEPRECIADO — mantido por um ciclo de release.
#
# Este arquivo NÃO é mais o ENTRYPOINT; /init é (via entrypoint-dispatch.sh).
# Existe apenas para que referências externas — compose antigos, scripts de
# operação, documentação de terceiros — não quebrem de uma vez.
#
# O CONTRATO MUDOU, e o aviso é explícito porque a diferença é silenciosa:
# o stage2 trata SÓ do bootstrap de cont-init (remap de UID, chown, seed de
# config, sync de skills); ele NÃO executa o CMD. Quem dependia do contrato
# anterior — "o entrypoint prepara o estado e então executa o kairos" — vai
# ver o bootstrap acontecer e o comando NÃO rodar.
set -euo pipefail

cat >&2 <<'WARN'
kairos: AVISO DE DEPRECIAÇÃO — docker/entrypoint.sh não é mais o ENTRYPOINT.
kairos: Use ENTRYPOINT ["/opt/kairos/docker/entrypoint-dispatch.sh"].
kairos: Este shim faz APENAS o bootstrap; ele NÃO executa o seu CMD.
kairos: Será removido no próximo major.
WARN

exec /opt/kairos/docker/stage2-hook.sh "$@"
