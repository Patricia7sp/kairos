#!/bin/bash
# Compila libfts5_cjk.so e instala em ~/.kairos/lib/ (ou em $1).
#
# Usa o sqlite3ext.h do sistema quando existe; caso contrário, a cópia em
# vendor/ (headers da amalgamação do SQLite, domínio público). Assim o build
# funciona sem libsqlite3-dev — exigir o pacote de desenvolvimento
# inviabilizaria a instalação para o usuário final, que é justamente quem
# mais precisa da busca CJK.
set -euo pipefail
cd "$(dirname "$0")"

CFLAGS_EXTRA=""
if ! echo '#include <sqlite3ext.h>' | gcc -E -xc - >/dev/null 2>&1; then
  CFLAGS_EXTRA="-Ivendor"
fi

gcc -shared -fPIC -O2 -Wall -Wextra $CFLAGS_EXTRA fts5_cjk.c -o libfts5_cjk.so

dest="${1:-${KAIROS_HOME:-$HOME/.kairos}/lib}"
mkdir -p "$dest"
# 0644: legível por todos, gravável só pelo dono. É código executado dentro
# do processo do banco — permissão de escrita ampla seria injeção de código.
install -m 0644 libfts5_cjk.so "$dest/libfts5_cjk.so"
echo "instalado: $dest/libfts5_cjk.so"
