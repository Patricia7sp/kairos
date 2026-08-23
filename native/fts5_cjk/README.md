# `fts5_cjk` — tokenizador CJK para FTS5

`cjk_unicode61` = `unicode61` + **bigramas de caractere CJK**, semântica do
`CJKAnalyzer` do Lucene.

## O problema

O `unicode61` trata uma sequência CJK como **um** token: `웅기가말했다` indexa
como um único token de 6 caracteres, e uma consulta de 2 caracteres nunca casa
dentro dele. O tokenizador `trigram` resolve substring mas exige ≥3 caracteres
por termo — palavras coreanas de 2 caracteres (`일본`, `구글`, `우리`) caíam em
varredura completa por `LIKE`, **medida em 3–6 s por consulta** numa tabela de
mensagens de 6,8 GB, e era o principal fator de latência da busca de sessões.

## A solução

Cada token que o `unicode61` emite é reexaminado. Sequências CJK maximais
viram bigramas **sobrepostos**; segmentos não-CJK passam intactos; um
caractere CJK isolado vira unigrama.

```
캘린더  →  [캘린] [린더]
```

Como o FTS5 transforma tokens consecutivos de um mesmo termo numa frase, a
consulta ganha semântica exata de substring com velocidade de índice.

## Build

```bash
./build.sh                    # instala em $KAIROS_HOME/lib (padrão ~/.kairos/lib)
./build.sh /outro/destino
```

Sem `libsqlite3-dev`: o script detecta a ausência do header do sistema e usa
`vendor/` (amalgamação do SQLite, domínio público).

## Ativação

É **por presença**. Instalada a extensão, o próximo open do banco cria o
índice `messages_fts_cjk`. Mesma disciplina dos outros índices: external
content, linhas de ferramenta excluídas.

| Controle | Efeito |
|---|---|
| `sessions.cjk_fts: false` | Desativa mesmo com o `.so` presente |
| `KAIROS_FTS5_CJK_SO` | Sobrescreve o caminho do `.so` |

## Backfill

Mensagens **novas** são indexadas ao vivo assim que o índice existe. Banco já
populado precisa de backfill explícito, que é **retomável por passos** com
marca d'água persistida — banco grande não pode depender de uma execução
ininterrupta.

## Degradação

A extensão é **opcional e sempre foi**. Sem ela, nenhuma consulta CJK é
tentada e a busca segue por FTS5 base → trigram → `LIKE`. Ausência reduz a
qualidade da busca; nunca derruba o agente.

## Procedência

Reconstruído para o Kairos a partir de `_reversa_sdd/native/`. O tokenizador
original do Hermes é contribuição externa de Soju06 (PR #65544).
