#!/usr/bin/env python3
"""Migra as skills do Hermes para o Kairos.

O que atravessa é a CAPACIDADE: o SKILL.md e os scripts que ele chama. O que
não atravessa é a marca — e aqui isso não é cosmético. Uma skill que procura
`$HERMES_HOME/.env` não acha nada rodando no Kairos: as referências são
funcionais, e traduzi-las é o que faz a skill funcionar do outro lado.

    python scripts/migrar_skills.py ../hermes-agent [--aplicar]

Sem `--aplicar` é uma simulação: relata o que faria e valida tudo, sem
escrever. Todo destino é validado com o `validate_frontmatter` do Kairos, para
que uma skill quebrada seja recusada aqui e não no meio de uma sessão.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from kairos_skills.frontmatter import (  # noqa: E402
    FrontmatterError,
    parse_frontmatter,
    validate_frontmatter,
)

# Ordem importa: o mais específico primeiro, senão `.hermes` come `HERMES_HOME`.
SUBSTITUICOES = [
    (re.compile(r"\bHERMES_HOME\b"), "KAIROS_HOME"),
    (re.compile(r"\bHERMES_"), "KAIROS_"),
    (re.compile(r"~/\.hermes\b"), "~/.kairos"),
    (re.compile(r"\$HOME/\.hermes\b"), "$HOME/.kairos"),
    (re.compile(r"\.hermes/"), ".kairos/"),
    (re.compile(r"\b_hermes_env\b"), "_kairos_env"),
    (re.compile(r"\bhermes_home\b"), "kairos_home"),
    (re.compile(r"\b_hermes_home\b"), "_kairos_home"),
    (re.compile(r"Hermes Agent"), "Kairos"),
    (re.compile(r"\bhermes CLI\b", re.I), "kairos CLI"),
    (re.compile(r"`hermes `?"), "`kairos "),
    # Por último, o termo em qualquer posição, preservando a caixa. Sem borda
    # de palavra de propósito: `get_hermes_home` e `hermes_constants` são
    # identificadores reais no código das skills, e `\b` não os alcança porque
    # `_` conta como caractere de palavra. As URLs já saíram de cena acima.
    (re.compile(r"Hermes"), "Kairos"),
    (re.compile(r"HERMES"), "KAIROS"),
    (re.compile(r"hermes"), "kairos"),
]


# Caminhos também carregam a marca: `_hermes_home.py` é importado pelo nome, e
# um diretório `hermes-agent/` vira o `name` da skill. Traduzir só o conteúdo
# deixaria imports quebrados apontando para arquivos que não existem mais.
def traduzir_nome(nome: str) -> str:
    for antigo, novo in (("hermes", "kairos"), ("Hermes", "Kairos")):
        nome = nome.replace(antigo, novo)
    return nome


# URLs do projeto de origem ficam: apontam para onde o código realmente veio, e
# reescrevê-las para um endereço do Kairos seria inventar um host que não existe.
URL_ORIGEM = re.compile(r"https?://[^\s`\"')]*hermes[^\s`\"')]*")


def traduzir(texto: str) -> str:
    urls = {}

    def guardar(m):
        chave = f"\x00URL{len(urls)}\x00"
        urls[chave] = m.group(0)
        return chave

    texto = URL_ORIGEM.sub(guardar, texto)
    for padrao, novo in SUBSTITUICOES:
        texto = padrao.sub(novo, texto)
    for chave, url in urls.items():
        texto = texto.replace(chave, url)
    return texto


def converter_frontmatter(bruto: str, categoria: str) -> str:
    """`metadata.hermes.{tags,related_skills}` sobe para o nível de topo.

    O Kairos lê tags e related_skills direto; aninhados sob o nome do produto
    de origem eles seriam simplesmente ignorados, e a skill perderia o
    roteamento por tag sem que nada acusasse.
    """
    linhas = bruto.splitlines()
    saida, extraidas, i = [], [], 0
    while i < len(linhas):
        ln = linhas[i]
        if re.match(r"^metadata:\s*$", ln):
            i += 1
            while i < len(linhas) and (linhas[i].startswith(" ") or not linhas[i].strip()):
                m = re.match(r"^\s+(tags|related_skills):\s*(.+)$", linhas[i])
                if m:
                    extraidas.append(f"{m.group(1)}: {m.group(2)}")
                i += 1
            continue
        saida.append(ln)
        i += 1
    saida.extend(extraidas)
    saida.append(f"category: {categoria}")
    texto = "\n".join(saida)
    # `author` aparece ora como texto, ora como lista, e o Kairos espera texto.
    # `authors:` (plural) é a mesma coisa com outro nome.
    texto = re.sub(r"^authors:", "author:", texto, flags=re.M)
    texto = re.sub(
        r"^author:\s*\[(.+?)\]\s*$",
        lambda m: "author: " + ", ".join(x.strip().strip("\"'") for x in m.group(1).split(",")),
        texto,
        flags=re.M,
    )
    return texto


def migrar_uma(origem: Path, destino: Path, categoria: str, aplicar: bool) -> tuple[bool, str]:
    texto = (origem / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", texto, re.DOTALL)
    if not m:
        return False, "sem bloco de frontmatter"
    novo = f"---\n{traduzir(converter_frontmatter(m.group(1), categoria))}\n---\n{traduzir(m.group(2))}"

    try:
        fm, _ = parse_frontmatter(novo)
        validate_frontmatter(fm)
    except FrontmatterError as e:
        return False, str(e)

    if aplicar:
        if destino.exists():
            shutil.rmtree(destino)
        shutil.copytree(origem, destino)
        (destino / "SKILL.md").write_text(novo, encoding="utf-8")
        # Renomeia de baixo para cima: renomear um diretório antes do que está
        # dentro dele invalidaria os caminhos ainda por percorrer.
        for f in sorted(destino.rglob("*"), key=lambda x: -len(x.parts)):
            traduzido = traduzir_nome(f.name)
            if traduzido != f.name:
                f.rename(f.with_name(traduzido))
        # Os scripts também carregam os caminhos do Hermes.
        for f in destino.rglob("*"):
            # Qualquer arquivo que decodifique como texto, e não uma lista de
            # extensões: a lista esquecia `.mjs` e `.yaml`, e a marca sobrevivia
            # justamente nos arquivos que ninguém lembra de incluir.
            if f.is_file() and f.name != "SKILL.md":
                try:
                    conteudo = f.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    continue
                traduzido = traduzir(conteudo)
                if traduzido != conteudo:
                    f.write_text(traduzido, encoding="utf-8")
    return True, fm.name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("origem", type=Path, help="raiz do hermes-agent")
    ap.add_argument("--aplicar", action="store_true", help="escreve de verdade")
    ap.add_argument("--incluir-opcionais", action="store_true")
    args = ap.parse_args()

    destino_raiz = RAIZ / "skills"
    fontes = [args.origem / "skills"]
    if args.incluir_opcionais:
        fontes.append(args.origem / "optional-skills")

    ok, falhas = [], []
    for fonte in fontes:
        if not fonte.is_dir():
            print(f"aviso: {fonte} não existe", file=sys.stderr)
            continue
        for skill_md in sorted(fonte.rglob("SKILL.md")):
            origem = skill_md.parent
            categoria = origem.relative_to(fonte).parts[0] if origem.parent != fonte else "geral"

            # A categoria é preservada no caminho. Achatar quebraria os SKILL.md
            # que se referem uns aos outros por caminho completo — github-code-review
            # chama `$KAIROS_HOME/skills/github/github-auth/scripts/...`, e sem o
            # nível da categoria esse arquivo deixa de existir.
            relativo = origem.relative_to(fonte)
            destino = destino_raiz / Path(*[traduzir_nome(x) for x in relativo.parts])
            sucesso, detalhe = migrar_uma(origem, destino, categoria, args.aplicar)
            (ok if sucesso else falhas).append((origem.name, detalhe))

    print(f"{'migradas' if args.aplicar else 'migráveis'}: {len(ok)}")
    if falhas:
        print(f"recusadas: {len(falhas)}")
        for nome, motivo in falhas:
            print(f"  {nome}: {motivo}")
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
