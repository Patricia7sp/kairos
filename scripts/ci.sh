#!/usr/bin/env bash
# Roda localmente os MESMOS passos do CI (.github/workflows/ci.yml).
#
# Existe por duas razões. A primeira é que o repositório ainda não tem
# remoto, então o workflow não executa em lugar nenhum — sem este script o CI
# seria aspiração, não verificação. A segunda vale mesmo depois: descobrir uma
# quebra no push é um ciclo de minutos; descobrir aqui é de segundos.
#
# Uso:
#   scripts/ci.sh          # tudo que estiver disponível
#   scripts/ci.sh --fast   # pula o que exige Docker
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

FAST=0
[ "${1:-}" = "--fast" ] && FAST=1

falhas=()
run() {
  local nome="$1"; shift
  printf '\n\033[1m▸ %s\033[0m\n' "$nome"
  if "$@"; then
    printf '  \033[32mok\033[0m\n'
  else
    printf '  \033[31mFALHOU\033[0m\n'
    falhas+=("$nome")
  fi
}
skip() { printf '\n\033[1m▸ %s\033[0m\n  \033[33mpulado — %s\033[0m\n' "$1" "$2"; }

UV="${UV:-uv}"
command -v "$UV" >/dev/null 2>&1 || UV="$HOME/.local/bin/uv"

run "Testes (unitários)" \
    "$UV" run pytest -q --deselect tests/test_container.py::RealImageTests

run "Lint (ruff check)"  "$UV" run ruff check .
run "Lint (ruff format)" "$UV" run ruff format --check .

if command -v shellcheck >/dev/null 2>&1; then
  # A expansão em palavras é intencional: uma lista de arquivos.
  # shellcheck disable=SC2046
  run "Lint de shell" shellcheck $(git ls-files '*.sh' 'docker/bin/*' 'docker/cont-init.d/*' 'scripts/*.sh')
else
  skip "Lint de shell" "shellcheck não instalado"
fi

run "Gate de recall (G-19)" "$UV" run pytest -q tests/test_evals.py

run "uv.lock em dia" "$UV" lock --check

for front in ui-tui web apps/desktop; do
  if ! command -v npm >/dev/null 2>&1; then
    skip "$front (vitest + tsc)" "npm não instalado"
  elif [ ! -d "$front/node_modules" ]; then
    skip "$front (vitest + tsc)" "rode: npm install --prefix $front"
  else
    run "$front: tsc"    npx --prefix "$front" tsc --noEmit -p "$front"
    run "$front: vitest" npm test --prefix "$front" --silent
  fi
done

if [ "$FAST" -eq 1 ]; then
  skip "Imagem + integração" "--fast"
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  # Invocada indiretamente por `run`.
  # shellcheck disable=SC2317
  hadolint_local() {
    docker run --rm -v "$PWD/.hadolint.yaml:/cfg.yaml:ro" -i \
      hadolint/hadolint:latest-alpine hadolint --config /cfg.yaml - < Dockerfile
  }
  run "Dockerfile (hadolint)" hadolint_local
  run "docker build --check" docker build --check .
  run "Construir a imagem"   docker build -q -t kairos:test .
  run "Testes de integração" "$UV" run pytest -q tests/test_container.py::RealImageTests
else
  skip "Imagem + integração" "docker indisponível"
fi

printf '\n'
if [ ${#falhas[@]} -eq 0 ]; then
  printf '\033[32m✓ tudo passou\033[0m\n'
  exit 0
fi
printf '\033[31m✗ falhou: %s\033[0m\n' "${falhas[*]}"
exit 1
