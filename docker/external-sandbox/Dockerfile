# syntax=docker/dockerfile:1
FROM python:3.11-slim-bookworm AS worker

ARG TARGETARCH
ARG CODEX_VERSION=0.154.0
ARG CODEX_SHA256=fc6e3e3b85f2cf7d664520ee5c66a7fe4aa12bae7d46834f47e2f165fd0d6f78

RUN apt-get update && apt-get install --no-install-recommends -y ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/* \
    && test "${TARGETARCH}" = "amd64" \
    && curl -fsSL "https://releases.openai.com/codex/releases/${CODEX_VERSION}/codex-package-x86_64-unknown-linux-musl.tar.gz" -o /tmp/codex.tar.gz \
    && printf '%s  %s\n' "${CODEX_SHA256}" /tmp/codex.tar.gz > /tmp/codex.sha256 \
    && sha256sum -c - < /tmp/codex.sha256 \
    && mkdir -p /usr/local/lib/codex \
    && tar -C /usr/local/lib/codex -xzf /tmp/codex.tar.gz \
    && ln -s /usr/local/lib/codex/bin/codex /usr/local/bin/codex \
    && rm /tmp/codex.tar.gz /tmp/codex.sha256 \
    && groupadd -g 10000 worker \
    && useradd -l -u 10000 -g 10000 -m -d /home/worker worker \
    && mkdir /workspace && chown worker:worker /workspace \
    && mkdir -p -m 0755 /usr/local/lib/kairos-worker

ENV HOME=/home/worker CODEX_HOME=/home/worker/.codex \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --chmod=0444 docker/external-sandbox/model_bridge.py /usr/local/lib/kairos-worker/model_bridge.py
COPY --chmod=0444 kairos_runtime/experimental/snapshot.py /usr/local/lib/kairos-worker/snapshot.py
USER 10000:10000
WORKDIR /workspace
CMD ["/bin/sleep", "infinity"]
