#!/usr/bin/env bash
# Smoke pós-deploy: confirma que a stack subiu e responde, após um deploy no
# Komodo. Roda no host que serve a stack (action/procedure do Komodo — ver
# docs/ci-cd-komodo.md), não no runner do GitHub.
#
# Fail-closed: qualquer falha de rede, formato inesperado ou status divergente
# vira saída não-zero com a causa em stderr — deploys que não respondem não
# são fingidos como sucesso.
#
# Ambiente:
#   KAIROS_HEALTH_URL        endpoint de saúde (default: http://127.0.0.1:9119/api/health)
#   KAIROS_EXPECTED_STATUS   valor de "status" considerado saudável (default: ok)
set -uo pipefail

KAIROS_HEALTH_URL="${KAIROS_HEALTH_URL:-http://127.0.0.1:9119/api/health}"
KAIROS_EXPECTED_STATUS="${KAIROS_EXPECTED_STATUS:-ok}"

command -v curl >/dev/null 2>&1 || { echo "erro: curl não instalado" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "erro: python3 não instalado" >&2; exit 1; }

echo "smoke: consultando $KAIROS_HEALTH_URL"
body="$(curl -fsS --max-time 10 "$KAIROS_HEALTH_URL")" || {
  echo "erro: /api/health não respondeu ($?)" >&2
  exit 1
}

status="$(printf '%s' "$body" | python3 -c '
import json, sys
try:
    doc = json.load(sys.stdin)
except json.JSONDecodeError as exc:
    raise SystemExit(f"resposta não é JSON válido: {exc}")
if "status" not in doc:
    raise SystemExit("resposta sem campo \"status\"")
print(doc["status"])
')" || {
  echo "erro: corpo inesperado do healthcheck:" >&2
  printf '%s\n' "$body" >&2
  exit 1
}

if [ "$status" != "$KAIROS_EXPECTED_STATUS" ]; then
  echo "erro: status \"$status\" (esperado \"$KAIROS_EXPECTED_STATUS\")" >&2
  printf '%s\n' "$body" >&2
  exit 1
fi

echo "smoke ok: $status"