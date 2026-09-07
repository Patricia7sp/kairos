#!/usr/bin/env bash
# Monitoring and startup gate, not a quota or automatic garbage collector.
set -euo pipefail
: "${KAIROS_HOME:?set KAIROS_HOME to the dedicated broker directory}"
max_kib=${KAIROS_MAX_HOME_KIB:-2097152}
min_free_kib=${KAIROS_MIN_FREE_KIB:-1048576}
[[ $max_kib =~ ^[1-9][0-9]{0,11}$ && $min_free_kib =~ ^[1-9][0-9]{0,11}$ ]] || exit 2
used_kib=$(du -sk -- "$KAIROS_HOME" | awk '{print $1}')
free_kib=$(df -Pk -- "$KAIROS_HOME" | awk 'END {print $4}')
printf 'Kairos storage: used_kib=%s free_kib=%s max_kib=%s min_free_kib=%s\n' \
    "$used_kib" "$free_kib" "$max_kib" "$min_free_kib"
if (( used_kib >= max_kib || free_kib < min_free_kib )); then
    echo 'Kairos storage threshold exceeded; operator review required.' >&2
    exit 1
fi
