#!/bin/bash
# Bootstrap de container. Roda como ROOT, depois de a árvore de supervisão
# subir e antes dos serviços de usuário.
#
# Quatro responsabilidades, e só estas: remap de UID/GID, chown de volume,
# seed de config, sync de skills.
#
# NÃO derruba privilégio. A queda acontece por serviço, no `run` de cada um
# (via s6-setuidgid) e no main-wrapper. Derrubar aqui tiraria do stage2 o
# root de que ele precisa para usermod/groupmod/chown.
#
# NÃO executa o CMD. Scripts de cont-init.d rodam sem argumentos — os args do
# usuário não são visíveis aqui. É por isso que bootstrap e execução do
# comando são coisas separadas.
set -euo pipefail

KAIROS_HOME="${KAIROS_HOME:-/opt/data}"
KAIROS_USER="${KAIROS_USER:-kairos}"
TARGET_UID="${KAIROS_UID:-10000}"
TARGET_GID="${KAIROS_GID:-$TARGET_UID}"

# --- 1. remap de UID/GID -----------------------------------------------------
# O volume montado do host tem o UID do host. Sem o remap, o usuário do
# container não consegue escrever no próprio HOME.
current_uid="$(id -u "$KAIROS_USER" 2>/dev/null || echo "")"
if [ -n "$current_uid" ] && [ "$current_uid" != "$TARGET_UID" ]; then
  echo "kairos: remapeando $KAIROS_USER de UID $current_uid para $TARGET_UID" >&2
  groupmod -o -g "$TARGET_GID" "$KAIROS_USER" 2>/dev/null || true
  usermod  -o -u "$TARGET_UID" -g "$TARGET_GID" "$KAIROS_USER"
fi

# --- 2. chown do volume ------------------------------------------------------
mkdir -p "$KAIROS_HOME"
chown -R "$TARGET_UID:$TARGET_GID" "$KAIROS_HOME" 2>/dev/null || true

# --- 3. seed de config -------------------------------------------------------
if [ ! -f "$KAIROS_HOME/config.yaml" ] && [ -f /opt/kairos/docker/config.default.yaml ]; then
  install -o "$TARGET_UID" -g "$TARGET_GID" -m 0644 \
    /opt/kairos/docker/config.default.yaml "$KAIROS_HOME/config.yaml"
fi

# --- 4. marcador de modo container -------------------------------------------
# Contrato com o CLI (RF-16): o fast path sonda a EXISTÊNCIA do arquivo, que é
# um stat barato; o caminho lento faz o parse do conteúdo. Por isso o formato
# é chave=valor simples, e não YAML — o CLI não pode pagar import de parser no
# caminho de boot.
umask 022
{
  echo "runtime=s6"
  echo "uid=$TARGET_UID"
  echo "gid=$TARGET_GID"
  echo "home=$KAIROS_HOME"
  echo "supervised=${KAIROS_SUPERVISED:-1}"
} > "$KAIROS_HOME/.container-mode"
chown "$TARGET_UID:$TARGET_GID" "$KAIROS_HOME/.container-mode"

# --- 5. sync de skills -------------------------------------------------------
if [ -d /opt/kairos/skills ]; then
  mkdir -p "$KAIROS_HOME/skills"
  cp -rn /opt/kairos/skills/. "$KAIROS_HOME/skills/" 2>/dev/null || true
  chown -R "$TARGET_UID:$TARGET_GID" "$KAIROS_HOME/skills"
fi
