#!/bin/bash
# Stub do gateway: ecoa o UID com que a service supervisionada roda e dorme.
# O run execlineb derruba o privilégio ANTES (s6-setuidgid), então o UID
# ecoado reflete o uid efetivo da service — o contrato que o teste verifica.
set -euo pipefail

echo "GATEWAY uid=$(id -u)"
exec /bin/sleep infinity