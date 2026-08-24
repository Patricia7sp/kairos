# syntax=docker/dockerfile:1
#
# Kairos — imagem de container.
#
# CICLO DE VIDA (caminho PID 1):
#   /init monta a árvore de supervisão
#     → roda /etc/cont-init.d/*  (ordem lexicográfica)
#     → sobe os serviços de s6-rc.d/
#     → executa o argv restante como MAIN PROGRAM, herdando
#       stdin/stdout/stderr do container — é isto que faz `--tui` funcionar
#   Quando o main program termina, /init inicia o stage 3 de shutdown e o
#   container sai COM O CÓDIGO DE SAÍDA DO PROGRAMA.

FROM python:3.11-slim-bookworm AS base

ARG S6_OVERLAY_VERSION=3.2.0.2
ARG KAIROS_UID=10000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KAIROS_HOME=/opt/data \
    HOME=/opt/data

RUN apt-get update && apt-get install --no-install-recommends -y \
        ca-certificates \
        curl \
        gcc \
        git \
        libc6-dev \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# --- s6-overlay --------------------------------------------------------------
# Substitui o tini: precisamos de árvore de supervisão real, não só de um
# reaper de zumbis. O gateway e o dashboard são serviços distintos, e um
# precisa poder cair sem derrubar o outro.
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz /tmp/
ADD https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-x86_64.tar.xz /tmp/
RUN tar -C / -Jxpf /tmp/s6-overlay-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-overlay-x86_64.tar.xz \
    && rm -f /tmp/s6-overlay-*.tar.xz

# --- usuário não-privilegiado -------------------------------------------------
# O container COMEÇA como root de propósito: o stage2 precisa de root para
# usermod/groupmod e para o chown do volume. Cada serviço supervisionado então
# derruba para este usuário no próprio `run`.
# `-l` é essencial com UID alto: sem ele o useradd cria entradas em
# /var/log/lastlog e /var/log/faillog indexadas por UID, produzindo arquivos
# esparsos de centenas de MB na imagem (hadolint DL3046).
RUN useradd -l -u ${KAIROS_UID} -m -d /opt/data kairos

# --- aplicação ---------------------------------------------------------------
WORKDIR /opt/kairos
COPY pyproject.toml README.md ./
# ATENÇÃO: esta lista tem de bater com [tool.setuptools].packages do
# pyproject.toml. São duas listas mantidas à mão, e a deriva entre elas só
# aparece num build real — `docker build --check` não a detecta. Há um teste
# que compara as duas (tests/test_container.py).
COPY kairos_state/ ./kairos_state/
COPY kairos_domain/ ./kairos_domain/
COPY kairos_i18n/ ./kairos_i18n/
COPY kairos_container/ ./kairos_container/
COPY kairos_tools/ ./kairos_tools/
COPY kairos_skills/ ./kairos_skills/
COPY kairos_plugins/ ./kairos_plugins/
COPY kairos_providers/ ./kairos_providers/
COPY kairos_gateway/ ./kairos_gateway/
COPY kairos_mcp/ ./kairos_mcp/
COPY kairos_agent/ ./kairos_agent/
COPY kairos_cron/ ./kairos_cron/
COPY kairos_cli/ ./kairos_cli/
COPY kairos_tui_host/ ./kairos_tui_host/
COPY kairos_acp/ ./kairos_acp/
COPY kairos_integration/ ./kairos_integration/
COPY kairos_evals/ ./kairos_evals/
COPY kairos_web/ ./kairos_web/
COPY skills/ ./skills/
COPY locales/ ./locales/
COPY native/ ./native/

RUN python -m venv /opt/kairos/.venv \
    && /opt/kairos/.venv/bin/pip install --no-cache-dir -e .

# Extensão CJK: opcional por desenho, mas na imagem o gcc existe, então vale
# compilá-la. Falha aqui não derruba o build — a busca degrada.
RUN /opt/kairos/native/fts5_cjk/build.sh /opt/kairos/lib || \
    echo "kairos: extensão CJK não compilou; a busca degrada para FTS5/trigram/LIKE" >&2

# --- scripts de container -----------------------------------------------------
COPY docker/ /opt/kairos/docker/
# `install` não cria o diretório de destino; `-D` cria só o pai do arquivo,
# então os diretórios vêm explícitos.
RUN mkdir -p /opt/kairos/bin /etc/cont-init.d /etc/s6-overlay/s6-rc.d \
    && install -m 0755 /opt/kairos/docker/bin/kairos /opt/kairos/bin/kairos \
    && install -m 0755 /opt/kairos/docker/cont-init.d/01-kairos-setup      /etc/cont-init.d/01-kairos-setup \
    && install -m 0755 /opt/kairos/docker/cont-init.d/015-supervise-perms  /etc/cont-init.d/015-supervise-perms \
    && install -m 0755 /opt/kairos/docker/cont-init.d/02-reconcile-profiles /etc/cont-init.d/02-reconcile-profiles \
    && cp -r /opt/kairos/docker/s6-rc.d/. /etc/s6-overlay/s6-rc.d/

# /opt/kairos/bin ANTES da venv: é o que faz o shim de exec ser alcançado
# primeiro por `docker exec <c> kairos ...`. Inverter esta ordem reintroduz o
# bug de UID.
ENV PATH="/opt/kairos/bin:/opt/kairos/.venv/bin:${PATH}" \
    KAIROS_FTS5_CJK_SO=/opt/kairos/lib/libfts5_cjk.so \
    S6_KEEP_ENV=1

VOLUME ["/opt/data"]
EXPOSE 8080

ENTRYPOINT ["/opt/kairos/docker/entrypoint-dispatch.sh"]
