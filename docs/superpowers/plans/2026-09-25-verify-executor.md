# Executor real do `kairos verify` — receita, fases e readiness

Fecha a lacuna deixada pelo recorte de CLI honesta (D-CLI.9): `verify`
passa de recusa 69 para efeito real — detectar a receita do projeto, rodar
bootstrap/build/test e provar readiness de porta.

Origem no legado: `hermes_cli/verify_cmd.py` + `agent/verify/{recipes,
environment,runner}.py` (port scoped de superagent-ai/grok-cli
`src/verify/*`). Referências de spec: `_reversa_sdd/hermes-cli/`.

## Contrato

`kairos verify [--path DIR] [--json] [--detect-only] [--save] [--phase F]
[--timeout S] [--ready-timeout S] [--skip-start] [--port N]`

1. Raiz = `--path` ou diretório corrente; não-diretório → exit 2.
2. Receita: manifesto salvo vence; senão, detecção estática na ordem do
   legado (package.json → Python → Go → Rust → Java → Makefile → compose).
   Sem receita reconhecível → exit 1 com a recusa honesta apontando o
   manifesto (e `--json` com `{ok:false,error:"no-recipe",root}`).
3. Fases `bootstrap`/`build`/`test` (comandos da própria receita, no
   checkout do projeto), depois `start` em background + poll de readiness —
   a menos de `--phase` explícito ou `--skip-start`. Fase falha para o lote
   (stop-on-failure, como o legado).
4. Saída humana ou JSON (`{ok, recipe, source, phases, readiness}`); exit
   0 só com tudo ok. `--save` grava o manifesto; `--detect-only` imprime a
   detecção e sai 0.

## Divergências deliberadas do Kairos (D-CLI.10)

- **Manifesto em `.kairos/environment.json`** (não `.hermes/…`): o namespace
  do próprio projeto; um reparo/alteração de receita é decisão do usuário no
  checkout que está verificando.
- **Sem ledger de evidência** (`record_verify_run` do legado): a
  `verification_evidence` só existia para satisfazer o *verify-on-stop guard*
  do runtime legado, que o Kairos não portou. Sem consumidor, gravar seria
  estado fabricado — a evidência é o `--json` + exit code. O `kairos apply`
  (entrega de runtime) executa seus próprios `--test`, independente do
  `verify`.
- **Sem `_merge_project_facts_commands`**: dependia de
  `agent.coding_context.detect_project_facts`, camada que o Kairos não tem; o
  detector cobre o mesmo terreno (scripts dev/build/test).
- **Sem executor de shell — `shlex.split` + recusa barulhenta (revisão da
  proposta com `shell=True`).** A intenção original (portar o `shell=True`,
  ferramenta de dev executando o próprio checkout) colidiu com o gate de
  security do repo: a auditoria SEC-SRC-004 classifica shell como HIGH e o
  teste de repositório falha em qualquer achado severo — a fronteira não cede
  (fail closed). O executor resolve o comando em argv com `shlex.split` e
  **recusa barulhento** comandos com metacaracteres que não honra fielmente
  (`&&`/`;`/`|`/`<`/`>`/globs/`$`/backtick/`~`; `$` e backtick também dentro
  de aspas duplas), com `UnsupportedCommand` + fase `unsupported` no JSON.
  Receitas com encadeamento pedem uma passada por comando no manifesto.
  A morte do grupo no teardown (`start_new_session` + killpg) é portada.

## Implementação

1. `kairos_cli/verify_recipe.py` — `Recipe` (to_dict/from_dict tolerante),
   `detect_package_manager`, `detect_recipe` e os detectores por kind
   (node/python/django/fastapi/flask/go/rust/java/make/compose), manifesto
   (`manifest_path`, `load_manifest`, `save_manifest`, `load_or_detect`).
2. `kairos_cli/verify_runner.py` — `PhaseResult`/`ReadinessResult`/
   `VerifyResult`, `command_argv` (shlex.split + recusa de metacaracteres),
   `_run_phase_command` (tail 2000, timeout), poll de readiness (HTTPError =
   up), `_terminate_process_group`, `run_verify`. [Feito]
3. `kairos_cli/verify.py` — substitui o stub pela entrada do comando
   (raiz/detecção/fases/relatório humano ou JSON/exit).
4. `kairos_cli/handlers.py` — `cmd_verify` passa a chamar `run_verify(args)`
   (sem asyncio, sem `_home` — a raiz é o projeto, não o home).
5. `kairos_cli/main.py` — args de `verify`: `--path`, `--save`,
   `--detect-only`, `--phase` (append), `--timeout`, `--ready-timeout`,
   `--skip-start`, `--port`.
6. Testes de comportamento (não snapshot): `tests/test_verify_recipe.py`
   (detecção por kind e prioridade, manifesto roundtrip/corrompido/vazio,
   port inválido), `tests/test_verify_runner.py` (fases reais em subprocesso
   com `true`/`false`/timeout, tail, stop-on-failure, readiness com
   `http.server` real + teardown do grupo), `tests/test_cli_verify.py`
   (end-to-end com projeto fake, `--json` shape, no-recipe exit 1,
   não-diretório exit 2). Atualizar `test_verify_nao_alega_sucesso` — seja
   feliz, `verify` agora tem efeito.
7. Registrar D-CLI.10 em `docs/decisoes.md`, progresso em
   `docs/functional-progress.md`. Suíte completa + `ruff`/`format` +
   ci.sh --fast.

## Fora de escopo

- Não toca no toolset do modelo (`kairos_tools` fica intacto — `verify` é CLI
  de desenvolvedor).
- Não cria storage novo (sem `verification_evidence`).
- Não promete detectar todo projeto do mundo: a ausência de receita é recusa
  honesta e ensina o manifesto.