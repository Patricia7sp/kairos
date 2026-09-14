# Contribuindo com o Kairos

Kairos é uma reimplementação do Hermes a partir das specs do Reversa. As regras
do repositório estão codificadas em [`AGENTS.md`](AGENTS.md) (incluídas
automaticamente por agentes de IA); este guia é o lado humano.

## O que o projeto valoriza

- **Corrigir bugs reais, bem** — reproduzir o sintoma na `main`, apontar a linha
  onde aparece e fechar a classe do bug, não só o ponto reportado.
- **Efeito real comprovado** — uma mudança que "reporta sucesso sem efeito" é
  pior que uma ferramenta ausente. O histórico tem exemplos dos dois lados
  (`permissions_respond` nunca é publicado justamente por isso).
- **Fronteiras fail-closed** — o runtime atesta o que o Docker retornou e recusa
  na divergência. Nunca "aceitar com aviso".
- **Teste como contrato de comportamento** — invariantes sobre como os dados se
  relacionam, nunca snapshot de valores atuais.

## Setup

```bash
uv venv --python 3.11          # ou: uv sync --python 3.11 --extra dev
uv pip install -e ".[dev]"
```

## Roda a suíte e o lint

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

- Testes de integração com Docker exigem as imagens construídas
  (`kairos:test`, `kairos:stub`; worker em `docker/external-sandbox/`). Sem a
  imagem, `RealImageTests` é pulado.
- Extensão CJK opcional: `./native/fts5_cjk/build.sh` (sem gcc a suíte passa
  igual; os testes que a exigem pulam).

## CI local, igual ao remoto

```bash
scripts/ci.sh          # os 8 jobs do CI em passos locais
scripts/ci.sh --fast   # pula o que exige Docker
```

Escrito porque rodar o CI inteiro leva minutos e o ciclo de feedback local,
segundos. Cada passo que falta é pulado com aviso, não em silêncio.

## Abrindo um PR

1. **Branche da `main`.** Escreva os testes primeiro (ou junto) do código.
2. **Rode a suíte completa** + `scripts/ci.sh` antes de abrir.
3. **Commits e título em português**, no estilo conventional:
   `feat(runtime):`, `test(providers):`, `fix(auth):`, `docs(runtime):`.
4. **O CI precisa fechar verde** antes do merge. Não há branch protection nem
   auto-merge: o gate é manual. Um job vermelho é motivo para não mergear.
5. **Squash-merge** é o padrão. Depois do merge, confira `git diff HEAD~1..HEAD`
   — um PR de branch desatualizada pode ter sobrescrito correções recentes.
6. Se mudou dependência: `uv lock` e `uv lock --check` verdes (lock desatualizado
   quebra o build da imagem, que resolve com `--frozen`).

## Convenções que o CI impõe (e por quê)

| Ferramenta | Job no CI | Nota |
|---|---|---|
| `pytest -m "not runtime_live"` | `tests` | `RealImageTests` deselecionado; roda no job `image` com a imagem construída |
| `ruff check` + `ruff format --check` | `lint` | config deliberada em `pyproject.toml`; `skills/` é conteúdo de terceiros e fica fora |
| `shellcheck` | `shell` | `-e SC1091`; marcadores s6 vazios em `ignore_names` |
| `hadolint` (`.hadolint.yaml`) | `dockerfile` | `Dockerfile` e `Dockerfile.stub`; versão pinada v2.12.0 para o stub |
| `docker build` + `RealImageTests` | `image` | `--check` valida estrutura; o build real pega deriva de COPY/PATH |
| `evals` | `evals` | gate de recall de compactação — queda reprova o PR |
| `uv lock --check` | `lockfile` | lock desatualizado quebra `--frozen` da imagem |

## Sobre o Hermes (o legado)

Este é um repositório de reimplementação: o código do Hermes vive em
`../hermes-agent/` e as specs em `../hermes-agent/_reversa_sdd/`. Use-os como
**referência de comportamento**, mas mude o Kairos quando a divergência for
deliberada e registrada em `docs/decisoes.md` — não para "espelhar o legado".