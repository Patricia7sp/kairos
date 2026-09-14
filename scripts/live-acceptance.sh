#!/usr/bin/env bash
# Aceite real (runtime_live) com credencial auto-expirável.
#
# A OpenAI não tem expiração programável de chave; a revogação é manual. Esta
# ferramenta aplica a expiração DO NOSSO LADO: guarda a chave em .env.live
# (gitignored, 0600) com um prazo de validade e se RECUSA a rodar o aceite
# depois dele — garantir que uma chave esquecida morra no disco, não no
# dashboard.
#
# Uso:
#   scripts/live-acceptance.sh store SK-....XX [dias]   # guarda (dias = 1 padrão, máx 7)
#   scripts/live-acceptance.sh run                      # roda o aceite (recusa se expirado)
#   scripts/live-acceptance.sh status                   # quanto falta da validade
set -u
cd "$(dirname "$0")/.." || exit 1

SECRETS=".env.live"
EXPIRES_KEY="KAIROS_RUNTIME_LIVE_EXPIRES_AT"
TOKEN_KEY="KAIROS_RUNTIME_LIVE_API_KEY"

cmd="${1:-status}"

die() { printf '\033[31mfalha: %s\033[0m\n' "$1" >&2; exit 1; }

hoje() { date -u +%F; }

ordinal() {
  local d="$1"
  date -u -d "$d" +%s 2>/dev/null || die "formato de data inválido: $d (use AAAA-MM-DD)"
}

store() {
  local token="${1:-}"
  local dias="${2:-1}"
  [ -n "$token" ] || die "token vazio"
  case "$token" in
    sk-*) ;;
    *) die "a chave deve começar com sk- (formato da OpenAI)" ;;
  esac
  case "$dias" in
    ''|*[!0-9]*) die "dias deve ser um inteiro" ;;
  esac
  if [ "$dias" -lt 1 ] || [ "$dias" -gt 7 ]; then
    die "dias deve estar entre 1 e 7"
  fi
  local expira
  expira="$(date -u -d "+${dias} day" +%F)" || die "falha ao calcular a validade"

  umask 077
  printf '%s=%s\n%s=%s\n' "$TOKEN_KEY" "$token" "$EXPIRES_KEY" "$expira" > "$SECRETS"
  printf 'chave guardada em %s (0600) até %s (%s dia(s))\n' "$SECRETS" "$expira" "$dias"
  printf 'Depois do aceite, revogue a chave no dashboard da OpenAI para que ela deixe de existir de fato.\n'
}

load() {
  [ -f "$SECRETS" ] || die "sem $SECRETS — rode 'scripts/live-acceptance.sh store SK-.... XX'"
  if [ "$(stat -c %a "$SECRETS")" != "600" ]; then
    printf '\033[33maviso: %s não está 0600; corrigindo\033[0m\n' "$SECRETS"
    chmod 600 "$SECRETS"
  fi
  # shellcheck disable=SC1090
  . "./$SECRETS"
  [ -n "${!TOKEN_KEY:-}" ] || die "chave vazia em $SECRETS"
  [ -n "${!EXPIRES_KEY:-}" ] || die "prazo ausente em $SECRETS"
}

check() {
  local expira="${!EXPIRES_KEY}"
  local hoje_s expira_s
  hoje_s="$(ordinal "$(hoje)")"
  expira_s="$(ordinal "$expira")"
  if [ "$hoje_s" -gt "$expira_s" ]; then
    printf '\033[31mchave expirada em %s — gere outra e rode store de novo\033[0m\n' "$expira"
    return 1
  fi
  local dias="$(( (expira_s - hoje_s) / 86400 ))"
  printf 'válida até %s (%s dia(s) restantes)\n' "$expira" "$dias"
}

case "$cmd" in
  store)
    store "${2:-}" "${3:-1}"
    ;;
  status)
    load
    check || exit 1
    ;;
  run)
    load || exit 1
    check || exit 1
    printf '\033[1m▸ aceite runtime_live\033[0m\n'
    KAIROS_RUNTIME_LIVE=1 \
      KAIROS_RUNTIME_LIVE_API_KEY="${!TOKEN_KEY}" \
      "${UV:-uv}" run pytest -q -m runtime_live tests/test_runtime_live.py
    ;;
  *)
    die "comando desconhecido: $cmd (use store|run|status)"
    ;;
esac