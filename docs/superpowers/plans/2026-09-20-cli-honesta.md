# CLI honesta — efeito real ou recusa barulhenta

Recorte das pendências de CLI: dar aos comandos que **alegam sucesso sem
efeito** (ou destróem sem salvaguarda) a semântica que a regra do projeto exige
— efeito real ou recusa barulhenta (exit 69). Nada aqui constrói subsistema
novo; muda o comportamento de superfície para parar de fingir.

## Contrato

Um comando declarado que reporta sucesso sem fazer nada é bug proposital
(AGENTS.md). Estado fabricado (exit 0) vira recusa com `ExitCode.NOT_IMPLEMENTED`
(69) e mensagem que **diz o que o efeito real exigiria**. Nenhum comando sai do
registro; todos mantêm handler e `Status.IMPLEMENTED` — a recusa informada É o
comportamento entregue (divergência deliberada, registrada em D-CLI.9).
`test_superficie_totalmente_implementada_sem_pendencias` continua valendo.

### Grupo 1 — estado fabricado vira recusa (69)

Apuração por consumidor real (grep em todo o código, fora de `kairos_cli/`):

| comando | hoje | evidência | depois |
| --- | --- | --- | --- |
| `verify` | exit 0 "Verificação concluída" só por achar 2 arquivos, com caminho errado | `..hermes-agent/hermes_cli/verify_cmd.py` exige executor de receitas (bootstrap/build/test + readiness) ausente no Kairos | recusa 69 apontando o executor |
| `acp status` | "nenhum editor conectado", exit 0 | `kairos_acp` só tem primitivas de protocolo; sem servidor/adapter | recusa 69 |
| `claw start/status` | status fabricado exit 0; start retorna 1 | sem run-time de browser no build | recusa 69 |
| `gui start/status` | status fabricado exit 0; start retorna 1 | sem app desktop no build | recusa 69 |
| `console start` | retorna 1 | `eval` é o efeito real; console interativo não existe | recusa 69 apontando `eval` |
| `update` | retorna 1 genérico | deploy na imagem vem do pipeline Komodo; dev via git | recusa 69 apontando a rota |
| `hooks` | grava `hooks.json` que nada lê | hooks de verdade vivem em `kairos_plugins.hooks` (37), registrados por plugins no runtime | recusa 69 |
| `skin` | grava `ui.theme` que nada lê | nenhuma interface do build consome a chave (web usa CSS próprio) | recusa 69 |
| `prompt-size` | grava `prompt_size` que nada lê | nenhum consumidor; `/api/ops/prompt-size` do painel não está registrado (dead) | recusa 69 |
| `pause` | grava/lê `autonomy.json` que nada lê | não há atividade autônoma a pausar neste build | recusa 69 |
| `pairing` | grava/lê `pairings.json` que nada lê | gateway sem fluxo de pareamento | recusa 69 |
| `peer` | grava/lê `peers.json` que nada lê | gateway sem peer | recusa 69 |

### Grupo 2 — destruição com salvaguarda

- `uninstall`: hoje apaga `$KAIROS_HOME` com `shutil.rmtree` sem confirmação.
  Passa a **fail-closed**: exige `--yes` (sem ele, imprime o que seria removido
  e recusa) e recusa dentro de container (o serviço é gerido pela stack; apagar
  o home de produção é catastrófico). O efeito real permanece com `--yes`.

## Implementação e validação

1. Reescrever os módulos-stub (`kairos_cli/{verify,acp,claw,gui,console,
   update,hooks,skin,prompt_size,pause,pairing,peer}.py`) para retornar
   `ExitCode.NOT_IMPLEMENTED` (69) com mensagem que aponta o efeito real
   ausente. `acp help` e `console eval` continuam informacionais/reais.
2. `uninstall`: adicionar flag global `--yes`; recusa sem ela e em container.
3. Regressões em `tests/test_cli_surface.py`: os testes que congelavam o estado
   fabricado (skin/prompt-size/peer/pairing/pause) passam a afirmar **que o
   comando refusa com 69 e não cria o arquivo/chave**; `inicios indisponíveis`
   passa a esperar 69; novos testes: `verify` não alega sucesso, `uninstall`
   sem `--yes` não remove, `uninstall --yes` remove home real, `uninstall`
   recusa com `.container-mode`.
4. Suíte dos afetados + `ruff check`/`format --check`, depois `scripts/ci.sh
   --fast`. Registrar progresso em `docs/functional-progress.md` e a divergência
   em `docs/decisoes.md` (D-CLI.9).