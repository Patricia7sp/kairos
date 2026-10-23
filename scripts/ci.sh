#!/usr/bin/env bash
# Roda localmente os MESMOS passos do CI (.github/workflows/ci.yml).
#
# Existe porque descobrir uma quebra no push é um ciclo de minutos e
# descobrir aqui é de segundos — e porque o build da imagem e os testes de
# integração rodam contra o daemon local, que é onde a stack de fato sobe.
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

run "Cliente v8 para regressão de downgrade" env UV="$UV" bash scripts/prepare-skill-v8-client.sh
run "Cliente v10 para regressão de downgrade" env UV="$UV" bash scripts/prepare-skill-v10-client.sh

# Cobertura para o Sonar (advisory), espelhando o job `tests` do CI. O
# `COVERAGE_PROCESS_START` ativa o `.pth` permanente do coverage em cada
# subprocesso; o `COVERAGE_FILE` absoluto mantém os data files no repositório.
: "${COVERAGE_PROCESS_START:=$PWD/pyproject.toml}"
: "${COVERAGE_FILE:=$PWD/.coverage}"
export KAIROS_COVERAGE_ROOT="$PWD"
export COVERAGE_PROCESS_START COVERAGE_FILE

run "Testes (unitários, com cobertura)" \
    "$UV" run coverage run -m pytest -q --deselect tests/test_container.py::RealImageTests
run "Combinar cobertura"       "$UV" run coverage combine
run "Relatório de cobertura"   "$UV" run coverage xml

# O scanner só roda com binário E token; sem eles, skip explícito — mesma forma
# dos skips de shellcheck/npm. Nunca reportar análise que não ocorreu.
if command -v sonar-scanner >/dev/null 2>&1 && [ -n "${SONAR_TOKEN:-}" ]; then
  run "Sonar (análise)" sonar-scanner
else
  skip "Sonar (análise)" "sonar-scanner ou SONAR_TOKEN ausente"
fi

run "Lint (ruff check)"  "$UV" run ruff check .
run "Lint (ruff format)" "$UV" run ruff format --check .

if command -v shellcheck >/dev/null 2>&1; then
  # A expansão em palavras é intencional: uma lista de arquivos.
  # shellcheck disable=SC2046
  # `skills/` fora, como no ruff: é conteúdo de terceiros, e os achados ali
  # não têm dono que possa corrigi-los.
  run "Lint de shell" shellcheck $(git ls-files '*.sh' 'docker/bin/*' 'docker/cont-init.d/*' 'scripts/*.sh' | grep -v '^skills/')
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
  # shellcheck disable=SC2317,SC2329
  hadolint_local() {
    docker run --rm -v "$PWD/.hadolint.yaml:/cfg.yaml:ro" -i \
      hadolint/hadolint:v2.12.0 hadolint --config /cfg.yaml - < "$1"
  }
  run "Dockerfile (hadolint)" hadolint_local Dockerfile
  run "Dockerfile.stub (hadolint)" hadolint_local Dockerfile.stub
  run "docker build --check" docker build --check .
  run "Construir a imagem"   docker build -q -t kairos:test .
  run "Construir o broker" docker build --target runtime-broker -t kairos:runtime-broker-test .
  run "Verificar o broker" docker run --rm --entrypoint docker kairos:runtime-broker-test --version
  run "Validar stub" docker build --check -f Dockerfile.stub .
  run "Construir stub" docker build -f Dockerfile.stub -t kairos:stub .
  run "Testes de integração" "$UV" run pytest -q tests/test_container.py::RealImageTests
  run "Validar worker" docker build --check -f docker/external-sandbox/Dockerfile .
  run "Construir worker" docker build -f docker/external-sandbox/Dockerfile -t kairos:external-sandbox .
  run "Sessões Docker isoladas (offline)" env KAIROS_EXTERNAL_SANDBOX_TEST=1 \
    timeout -k 10s 240s "$UV" run pytest -q \
    tests/test_external_sandbox_integration.py tests/test_docker_session_worker.py \
    tests/test_docker_sessions_integration.py
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
