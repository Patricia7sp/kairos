#!/usr/bin/env bash
# Python offline no ambiente de desenvolvimento; checks de Docker ficam no host.
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run --no-sync pytest -q \
    --deselect tests/test_container.py::RealImageTests \
    --deselect tests/test_container.py::DockerfileTests::test_docker_build_check_nao_reporta_warning \
    --deselect tests/test_compose_config.py::test_compose_configura_fallback_criptografado_com_segredo_somente_leitura \
    --deselect tests/test_compose_config.py::test_compose_nao_eleva_runtime_nem_expoe_docker_socket \
    "$@"
