# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS node
FROM kairos:external-sandbox AS development

USER root
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && pip install --no-cache-dir uv==0.12.5

COPY --chown=10000:10000 . /workspace/kairos
USER 10000:10000
WORKDIR /workspace/kairos
RUN uv sync --frozen --extra dev \
    && npm --prefix web ci \
    && npm --prefix ui-tui ci \
    && npm --prefix apps/desktop ci
ENV KAIROS_HOME=/home/worker/kairos-dev
CMD ["/bin/sleep", "infinity"]
