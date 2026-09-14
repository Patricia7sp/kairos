# Kairos — guia para agentes de IA e desenvolvedores

Kairos é a **reimplementação** do Hermes a partir das especificações geradas pelo
Reversa (não é fork). As specs ficam em `../hermes-agent/_reversa_sdd/`; as
**divergências deliberadas** em relação ao legado estão em `docs/decisoes.md`.

Antes de mudar qualquer coisa: a origem fica nas referências de spec
(`source_ref`, `unit=`, `enforced_at`) — a rastreabilidade pertence a elas, não
a comentários novos.

## Princípios

- **Sem efeito real é pior que ausência.** Uma ferramenta/comando que "reporta
  sucesso" sem fazer nada é um bug proposital; recusar barulhento (exit 77/69)
  é melhor que fingir. A mesma regra vale para testes que passam sem executar o
  caminho que afirmam cobrir.
- **Fail closed.** Fronteiras de segurança do runtime (Docker, sandbox, aprovação)
  atestam o estado observado e recusam na divergência — nunca "aceitam apesar de".
- **Gate de merge = CI verde.** Não existe branch protection; a disciplina é
  manual e obrigatória: a suíte inteira local + os 8 jobs do CI verdes antes do
  merge (quando aplicar, incluir *deselect*s/opt-ins).
- **Comportamento real, não snapshot.** Testes afirmam contratos/invariantes
  (como dois dados devem se relacionar), nunca congelam valores atuais (listas
  de modelos, contagens, versões de config).
- **O core é um cinto estreito.** Ferramentas/modelos novos pagam footprint em
  cada request. A regra do legado se mantém: preferir estender código → comando
  CLI + skill → ferramenta gated por `check_fn` → plugin/MCP → ferramenta nova
  (último recurso).

## Ambiente

- `uv` + **Python 3.11** fixado (`.python-version`). O Python do sistema pode
  ser outro; não dependa do interpretador global.
- Bootstrap:
  ```bash
  uv venv --python 3.11
  uv pip install -e ".[dev]"
  ```

## Testes

A suíte é escrita em `unittest` da biblioteca padrão (`tests/`).

```bash
uv run pytest -q                          # caminho normal
.venv/bin/python -m unittest discover -s tests   # sem dependência nenhuma
```

- `runtime_live` é marcador e **não rola** por padrão (`addopts = "-m 'not
  runtime_live'"`): exige credenciais reais. Não dê sinais de vida a esse caminho
  sem elas.
- `RealImageTests` (em `tests/test_container.py`) exigem a imagem **`kairos:test`**
  construída e são pulados na ausência dela — e deselecionados no job unitário do
  CI (ficam no job `image`).
- Regras de teste que valem o dobro aqui:
  - **Nunca ler o código-fonte em teste.** Um teste que lê um `.py` está testando
    o formato do código, não o comportamento. Extraia a lógica numa função
    testável e chame-a de verdade.
  - **Não escrever change-detector tests** (catálogos de modelos, contagem de
    ferramentas, versões de schema).
  - Testes de gancho/isolamento usam o padrão existente: monkeypatch de
    operações de daemon (`shutil.which` para docker etc.), não de mocks que
    escondem o caminho real.
- Testes de integração opcionais exigem env: `KAIROS_EXTERNAL_SANDBOX_TEST=1`,
  `KAIROS_RUNTIME_SANDBOX_TEST=1` (sandbox interna), `KAIROS_RUNTIME_LIVE=1`.

## Lint

```bash
uv run ruff check .
uv run ruff format --check .
```

A configuração em `pyproject.toml` é **deliberada** — cada família e cada ignore
tem um motivo anotado. Não contorne com `# noqa` genérico nem mude o `select`
para calar um achado; corrija o código. Destaques:

- `skills/` é conteúdo instalável de terceiros e fica fora do ruff — não é para
  "corrigir" ali.
- Import tardio é padrão do projeto (corte de ciclo entre camadas e boot barato);
  `PLC0415` está na lista de ignore por isso.
- `E501` ignorado: o formatador cuida.
- Tradução: o código é em português; `RUF001/002/003` ignorados.

## Shell e Dockerfile

- Shell: `shellcheck` (CI com `SHELLCHECK_OPTS=-e SC1091`; `ignore_names:
  "main-kairos runtime"` são marcadores s6 vazios, `ignore_paths: skills`).
- Dockerfile: `hadolint` (config `.hadolint.yaml`). O `Dockerfile.stub` também é
  lintado — mesmo sendo imagem de teste, é receita. Versão pinada `v2.12.0`.
- `docker build --check` valida **estrutura**; o build real pega o que ele não
  alcança (deriva entre `pyproject.toml` e `COPY`, diretório de destino, PATH de
  runtime). Foram três bugs assim — por isso o build entra no CI.

## Imagem de container

```bash
docker build -t kairos:test .                                  # imagem real
docker build -f Dockerfile.stub -t kairos:stub .               # stub s6 (~segundos)
docker build -f docker/external-sandbox/Dockerfile -t kairos:external-sandbox .   # worker
```

- O `Dockerfile.stub` espelha a ESTRUTURA da imagem real (s6, dispatch, shim,
  stage2, cont-init) trocando o pesado por fantasma — é o alvo dos testes de
  ciclo de vida s6.
- O Codex é pinado por versão **e** SHA (`ARG CODEX_VERSION=0.154.0` + sha256
  do tarball verificado). Ao atualizar, atualize o teste `DockerfileTests` que
  afirma o pin.

## CI

`.github/workflows/ci.yml` — um workflow, 8 jobs por ferramenta (não por
superfície): testes, lint, shell, dockerfile, imagem+integração, frontends,
gate de recall, `uv lock --check`. Actions fixadas por **SHA**, nunca por tag.

`scripts/ci.sh` roda exatamente os mesmos passos localmente; `--fast` pula o que
exige Docker. Prefira ele a reproduzir o CI na mão.

## Entrega

- Commits e títulos de PR em **português**, conventional commits
  (`feat(runtime):`, `test(providers):`, `fix(auth):`, `docs(runtime):`).
- Squash-merge é o padrão; após merge, verifique o diff `HEAD~1..HEAD` para varrer
  remoções acidentais (um PR de branch velha sobrescreve correções recentes).
- `uv lock` atualizado junto de qualquer mudança em dependências
  (`uv lock --check` é job próprio — lock desatualizado passa local e quebra o
  build da imagem, que resolve com `--frozen`).