#!/usr/bin/env bash
# Provisiona o cliente anterior real usado no teste obrigatório de downgrade.
set -euo pipefail
cd "$(dirname "$0")/.."

revision=44d890ca632b40b78a21b62c5e05622200a5b23c
fixture_root="$PWD/.superpowers/test-fixtures/skill-client-v10"
uv_command="${UV:-uv}"
mkdir -p "$fixture_root/.venv"
stage=$(mktemp -d "$fixture_root/.venv/build.XXXXXXXX")
mkdir "$stage/source" "$stage/wheels"

if ! git cat-file -e "$revision^{commit}" 2>/dev/null; then
  git fetch --no-tags --depth=1 origin "$revision"
fi
git archive "$revision" | tar -x -C "$stage/source"
source_epoch=$(git show -s --format=%ct "$revision")
printf 'setuptools==75.8.0\nwheel==0.45.1\n' > "$stage/build-constraints.txt"
SOURCE_DATE_EPOCH="$source_epoch" "$uv_command" build --python 3.11 --wheel \
  --build-constraints "$stage/build-constraints.txt" --out-dir "$stage/wheels" "$stage/source" \
  > "$stage/build.log" 2>&1 || { cat "$stage/build.log" >&2; exit 1; }

"$uv_command" export --frozen --no-dev --no-emit-project --format requirements-txt \
  --output-file "$stage/requirements.txt" > "$stage/export.log"
"$uv_command" venv --python 3.11 "$stage/venv"
"$uv_command" pip sync --python "$stage/venv/bin/python" --require-hashes "$stage/requirements.txt"
wheels=("$stage"/wheels/*.whl)
if [ "${#wheels[@]}" -ne 1 ] || [ ! -f "${wheels[0]}" ]; then
  printf 'Fixture v10: era esperado exatamente um wheel.\n' >&2
  exit 1
fi
"$uv_command" pip install --python "$stage/venv/bin/python" --no-deps "${wheels[0]}"
"$uv_command" pip check --python "$stage/venv/bin/python"

"$stage/venv/bin/python" -I - "$revision" "${wheels[0]}" "$stage" "$fixture_root" "$PWD/uv.lock" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

import kairos_cli
from kairos_state.schema import SCHEMA_VERSION

revision, wheel, stage, fixture_root, lock = sys.argv[1:]
stage = Path(stage)
module = Path(kairos_cli.__file__)
if sys.version_info[:2] != (3, 11) or SCHEMA_VERSION != 10 or not module.is_relative_to(stage / "venv/lib"):
    raise SystemExit("Cliente v10 incompatível ou importado fora do ambiente isolado")
provenance = {
    "revision": revision,
    "wheel": wheel,
    "sha256": hashlib.sha256(Path(wheel).read_bytes()).hexdigest(),
    "python": str(stage / "venv/bin/python"),
    "lock_sha256": hashlib.sha256(Path(lock).read_bytes()).hexdigest(),
    "requirements_sha256": hashlib.sha256((stage / "requirements.txt").read_bytes()).hexdigest(),
    "isolation_verification": str(module),
    "build_command": "scripts/prepare-skill-v10-client.sh",
}
destination = Path(fixture_root) / "provenance.json"
temporary = stage / "provenance.json"
temporary.write_text(json.dumps(provenance, indent=2) + "\n")
temporary.replace(destination)
print(json.dumps(provenance, indent=2))
PY
