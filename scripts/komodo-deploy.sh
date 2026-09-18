#!/usr/bin/env bash
# Dispara o deploy da stack no Komodo e aguarda o Update terminar com sucesso.
#
# Ponto de uso: job `deploy` do CI (push para main com os demais jobs verdes) e
# `workflow_dispatch` manual. O mesmo script reproduz o passo na mão — o que o
# CI chama é exatamente isto.
#
# Contrato com o Komodo (API REST):
#   POST /execute/DeployStack  {"stack": "<nome>"}        -> Update
#   POST /read/GetUpdate       {"id": "<update_id>"}      -> Update
# A autenticação é X-Api-Key (e, se existir, X-Api-Secret); ambos vêm do
# ambiente e nunca de argumentos (evitaria o valor em `ps`).
#
# Não imprime chave nenhuma. Fecha com erro se o gatilho não estiver
# configurado (fail-closed) e se o Update terminar em falha ou timeout.
#
# Ambiente:
#   KOMODO_HOST            URL do Core alcançável por quem roda o script
#   KOMODO_API_KEY         chave de API (obrigatória)
#   KOMODO_API_SECRET      segredo de API (opcional)
#   KOMODO_STACK           nome da stack no Komodo (default: kairos)
#   KOMODO_DEPLOY_TIMEOUT  segundos esperando o Update (default: 900)
#   KOMODO_POLL_INTERVAL   segundos entre polls (default: 10)
set -uo pipefail

KOMODO_HOST="${KOMODO_HOST:-}"
KOMODO_API_KEY="${KOMODO_API_KEY:-}"
KOMODO_API_SECRET="${KOMODO_API_SECRET:-}"
KOMODO_STACK="${KOMODO_STACK:-kairos}"
KOMODO_DEPLOY_TIMEOUT="${KOMODO_DEPLOY_TIMEOUT:-900}"
KOMODO_POLL_INTERVAL="${KOMODO_POLL_INTERVAL:-10}"

[ -n "$KOMODO_HOST" ] || {
  echo "erro: KOMODO_HOST ausente (secret do GitHub: URL do Komodo Core)" >&2
  exit 1
}
[ -n "$KOMODO_API_KEY" ] || {
  echo "erro: KOMODO_API_KEY ausente (secret do GitHub)" >&2
  exit 1
}
command -v curl >/dev/null 2>&1 || { echo "erro: curl não instalado" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "erro: python3 não instalado" >&2; exit 1; }

# Lê um campo de um JSON recebido pela stdin (via python3, presente no runner e
# no host — evita depender do jq). Exit 1 com mensagem se o campo não existir.
pyfield() {
  python3 -c '
import json, sys
expr = sys.argv[1]
doc = json.load(sys.stdin)
for part in expr.split("."):
    if not part:
        continue
    if isinstance(doc, dict) and part in doc:
        doc = doc[part]
        continue
    raise SystemExit("campo ausente no JSON: " + expr)
print(doc)
' "$1"
}

# Chamada autenticada à API. Não roda dentro de `$(...)` nos call-sites: uma
# substituição de comando cria um subshell e perderia as globais aí atribuídas.
# Em vez disso seta KOMODO_HTTP_CODE e KOMODO_BODY, globais do shell pai, e o
# corpo vai para stderr/saída minguada — nenhuma chave é impressa.
KOMODO_HTTP_CODE=000
KOMODO_BODY=""
komodo_call() {
  local path="$1" body="$2"
  local -a extra=()
  [ -n "$KOMODO_API_SECRET" ] && extra=(-H "X-Api-Secret: $KOMODO_API_SECRET")
  local body_file
  body_file="$(mktemp)"
  if KOMODO_HTTP_CODE="$(curl -sS -o "$body_file" -w '%{http_code}' \
    -X POST "$KOMODO_HOST/$path" \
    -H 'Content-Type: application/json' \
    -H "X-Api-Key: $KOMODO_API_KEY" \
    "${extra[@]}" \
    -d "$body")"; then
    KOMODO_BODY="$(cat "$body_file")"
    rm -f "$body_file"
    return 0
  fi
  rm -f "$body_file"
  echo "erro: chamada $path não respondeu (curl falhou)" >&2
  return 1
}

start=$(date +%s)
echo "deploy da stack '$KOMODO_STACK' em $KOMODO_HOST"

# --- 1. Dispara o deploy -------------------------------------------------
komodo_call execute/DeployStack "$(printf '{"stack":"%s"}' "$KOMODO_STACK")" || exit 1
if [ "$KOMODO_HTTP_CODE" -ge 400 ]; then
  echo "erro: Komodo recusou o deploy (HTTP $KOMODO_HTTP_CODE)" >&2
  printf '%s\n' "$KOMODO_BODY" >&2
  exit 1
fi
update_id="$(printf '%s' "$KOMODO_BODY" | pyfield _id.\$oid)" || {
  echo "erro: resposta de DeployStack sem update id:" >&2
  printf '%s\n' "$KOMODO_BODY" >&2
  exit 1
}
echo "update: $update_id"

# --- 2. Aguarda o Update terminar -----------------------------------------
deadline=$(( start + KOMODO_DEPLOY_TIMEOUT ))
while :; do
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    echo "erro: deploy não terminou em ${KOMODO_DEPLOY_TIMEOUT}s (update $update_id)" >&2
    exit 1
  fi
  sleep "$KOMODO_POLL_INTERVAL"

  komodo_call read/GetUpdate "$(printf '{"id":"%s"}' "$update_id")" || exit 1
  if [ "$KOMODO_HTTP_CODE" -ge 400 ]; then
    echo "erro: Komodo recusou o poll do update (HTTP $KOMODO_HTTP_CODE)" >&2
    printf '%s\n' "$KOMODO_BODY" >&2
    exit 1
  fi
  status="$(printf '%s' "$KOMODO_BODY" | pyfield status)" || {
    echo "erro: resposta de GetUpdate sem status:" >&2
    printf '%s\n' "$KOMODO_BODY" >&2
    exit 1
  }

  if [ "$status" = "Queued" ] || [ "$status" = "InProgress" ]; then
    echo "[$((now - start))s] aguardando ($status)"
    continue
  fi
  if [ "$status" != "Complete" ]; then
    echo "erro: status inesperado '$status' no update $update_id" >&2
    exit 1
  fi

  # O Update não tem campo `error`; a causa da falha está nos logs. Extrai os
  # últimos estágios para a mensagem, sem quebrar o script se o formato mudar.
  logsummary="$(printf '%s' "$KOMODO_BODY" | python3 -c '
import json, sys
doc = json.load(sys.stdin)
for entry in doc.get("logs", [])[-6:]:
    print(entry.get("stage", "?"), "-", (entry.get("content", "") or "")[:300])
' 2>/dev/null || true)"

  success="$(printf '%s' "$KOMODO_BODY" | pyfield success)" || exit 1
  if [ "$success" = "True" ]; then
    commit="$(printf '%s' "$KOMODO_BODY" | pyfield commit_hash 2>/dev/null || echo "?")"
    echo "deploy ok (update $update_id, commit '$commit')"
    exit 0
  fi
  echo "erro: deploy falhou (update $update_id):" >&2
  printf '%s\n' "$logsummary" >&2
  exit 1
done