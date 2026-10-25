# Índice automático de skills na CLI — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para execução nativa e sequencial pelo Root. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** disponibilizar automaticamente o catálogo instalado no Chat da CLI, em turnos únicos e interativos, com exigência e desligamento explícitos.

**Architecture:** conservar o booleano de exigência existente e adicionar intenção automática exclusiva da CLI. Dentro da posse do turno, resolver uma única seleção de modelo e derivar o envelope efetivo; compartilhar a captura ancorada estrita com uma variante que distingue ausência benigna de erro. Reusar snapshots positivos e a capacidade `skill_view` atual.

**Tech Stack:** Python 3.11, SQLite/arquivos reais, testes existentes pytest/unittest, Docker e CI existentes; sem schema, manifesto ou dependência nova.

**Spec:** [desenho aprovado](../specs/2026-10-25-indice-automatico-cli-design.md), commit `a8fc2236b2cc38b66641265a9dd871e32347a295`, SHA-256 `d364fa009661066829552ed17d77e1328dd6668f2f19a9ca93f26b9aebc8fa1e`.

**Autorização:** a usuária aprovou o desenho e respondeu “aprovado, pode seguir” ao pedido de seguir ao plano e à implementação. Método nativo preserva o agente único informado e as preferências do projeto; revisão independente obrigatória no fechamento. Este plano decompõe o desenho aprovado, sem reabrir decisões nem inferir autorização de merge/deploy.

## Global Constraints

- “Não mudar schema, versão do manifesto, tabelas, limites ou dependências.”
- “Preservar `InteractionEnvelope.skills_catalog: bool = False` para os consumidores atuais”; acrescentar `skills_catalog_auto: bool = False`, estrito e exclusivo da CLI.
- “A seleção de modelo é resolvida uma única vez, dentro da posse do turno, e congelada para a decisão e a requisição.”
- “A estabilidade começa na primeira ativação bem-sucedida”; não persistir catálogo vazio ou estado negativo.
- “No modo `off`, não emitir aviso de catálogo.” Não consultar catálogo/skills quando desligado ou modelo automático inapto.
- “Emitir no máximo um aviso de catálogo por turno, depois de `turn_start` e antes do primeiro conteúdo do provider.” CLI envia ready/skipped a stderr, inclusive JSON.
- “256 skills, 32 KiB de índice e 128 KiB de páginas por turno” permanecem os limites existentes.
- Agent Runtime, Web, gateways, ACP e demais interfaces ficam fora; preservar leitura sob demanda, autorização, recusa de overrides, cancelamento e posse.
- Arquivos/SQLite reais; não ler fonte Python em teste, congelar contagens do bundle, silenciar lint ou acionar provider pago/runtime_live.

## Review Focus

1. `ENOENT` no home ou em componente anterior não é a ausência benigna do `skills` final; links/permissões/I/O nunca viram sucesso — T1.
2. Diretórios só com entradas inválidas não são catálogo vazio; nenhuma recaptura encobre snapshot corrompido ou cache órfão — T1/T2.
3. Modelo `tools=False` ou desconhecido continua o chat sem qualquer I/O de catálogo; seleção realmente inválida continua erro — T2.
4. Alternar auto/on/off, reiniciar e instalar após vazio preserva a primeira versão positiva e mantém histórico já lido — T2/T3.
5. Interativo, envelopes de runtime e múltiplas rodadas/retries não perdem intenção nem compartilham capacidade, orçamento ou seleção — T2/T3.

---

## Mapa de arquivos

| Arquivo | Responsabilidade |
| --- | --- |
| `kairos_skills/catalog_io.py` | Captura compartilhada; ausência final tipada sem enfraquecer API estrita |
| `kairos_integration/interaction_contract.py` e `event_protocol.py` | Intenção automática, motivo de skip validado e serialização |
| `kairos_integration/skill_catalog_turn.py` | Preparação opcional com persistência/capacidade existentes |
| `kairos_integration/interaction_service.py` | Decisão sob posse, seleção congelada, envelope efetivo e aviso único |
| `kairos_integration/router.py` | Veto da intenção automática em runtime |
| `kairos_cli/main.py` e `chat.py` | Três modos, propagação no interativo e avisos stderr |
| `tests/test_skill_catalog_io.py` | Recusas e ausência benigna com filesystem real |
| `tests/test_interaction_skill_catalog_auto.py` | Nova vertical auto/on/off com SQLite/adapter reais |
| `tests/test_cli_skill_catalog.py`, `test_interaction_contract.py` e testes de router/protocolo existentes | Comandos reais e compatibilidade dos contratos |
| `docs/decisoes.md`, `docs/cli-auth-progress.md` e guia CLI pertinente | Novo padrão, opt-out e estabilidade; nenhuma promessa de outras superfícies |

### Task 1: descoberta opcional sem mudar o leitor estrito

**Files:** modificar `kairos_skills/catalog_io.py`; testar `tests/test_skill_catalog_io.py`.

**Interfaces:** produzir `capture_optional_skill_catalog(home: Path) -> SkillCatalogSnapshot | None`; `None` somente para home aberto com segurança e `skills` final ausente, ou raiz válida sem candidatos/omissões. Conservar `capture_skill_catalog(home: Path) -> SkillCatalogSnapshot` estrita. Extrair `_capture_skill_catalog(home, *, allow_empty: bool)` e permitir ausência final em `_root`, sem mudar os demais leitores.

- [x] **RED:** acrescentar `test_optional_empty_preserves_strict_capture`, parametrizado ausente/vazio; `test_optional_invalid_or_unobservable_is_not_empty`, com home ausente, link em componente, apenas entradas inválidas e OSError sanitizado. Asserções centrais:

```python
assert capture_optional_skill_catalog(home) is None
with pytest.raises(SkillCatalogError):
    capture_skill_catalog(home)
```

  Para os casos inseguros, ambas as APIs levantam `SkillCatalogError`, sem conteúdo ou path privado na mensagem. Injeção de EACCES/EIO altera apenas `os.open` no componente real visado e chama o original nos demais. Comparar snapshot positivo das duas APIs com instalação real.
- [x] **Verificar RED:** `.venv/bin/python -m pytest -q tests/test_skill_catalog_io.py`; falhas devem demonstrar API opcional ausente, não erro de fixture.
- [x] **GREEN:** compartilhar captura; capturar `FileNotFoundError` somente na abertura ancorada do componente final `skills` quando permitido. Vazio com omissões continua erro; limitações e leitura de assets atuais permanecem.
- [x] **Verificar:** repetir o arquivo e `tests/test_skill_catalog.py tests/test_skill_catalog_state.py`; Ruff e diff-check dos arquivos alterados.
- [x] **Commit:** `feat(skills): distingue ausência benigna na captura automática`.

### Task 2: intenção automática e decisão dentro do turno

**Files:** modificar contrato, protocolo, `skill_catalog_turn.py`, `interaction_service.py` e router do mapa; criar `tests/test_interaction_skill_catalog_auto.py`; ampliar testes de contrato/protocolo/router existentes.

**Interfaces:** consumir T1. Produzir `InteractionEnvelope.skills_catalog_auto: bool = False`, mutuamente exclusivo com `skills_catalog`, ambos somente `source="cli"`. Acrescentar `InteractionEventKind.SKILL_CATALOG_SKIPPED` e campo opcional `catalog_skip_reason`, aceitando somente `empty`/`model_tools_unavailable` para esse evento. Produzir `prepare_skill_catalog_turn(..., allow_empty: bool = False) -> SkillCatalogTurn | None`, mantendo default estrito e reusando snapshot positivo antes da captura. O serviço guarda motivo efêmero por turno, sem cache global ou persistência nova.

- [x] **RED de contrato:** `test_auto_intent_is_strict_exclusive_and_cli_only` exige rejeição de inteiro/string/combinação on+auto e de fontes não CLI; `test_skipped_event_validates_and_serializes_reason` verifica motivo tipado, ausência de nomes/digest e campos antigos do protocolo preservados.
- [x] **RED da vertical:** novo arquivo reusa `_service`, `_view` e `_close` dos testes reais do catálogo; parametrizar persistência síncrona/assíncrona. Testes e invariantes:
  - `test_auto_positive_reuses_snapshot_and_reads_on_demand`: provider recebe só índice/`skill_view`; segunda rodada recebe corpo lido e o mesmo bloco system.
  - `test_auto_unsupported_model_skips_without_catalog_io`: tools False/None; spies em captura e `SkillCatalogRepository.get` falham se chamados; provider normal responde, um skip entre start e delta.
  - `test_auto_empty_can_activate_later_but_positive_snapshot_stays_fixed`: ausência/vazio não cria linha; instalar ativa; editar, desligar, reiniciar e religar preserva digest/bytes antigos.
  - `test_auto_corruption_and_invalid_installation_refuse_before_provider`: snapshot adulterado/cache órfão/instalação só inválida recusam; sem fallback de captura, mensagem sanitizada.
  - `test_auto_retries_keep_one_selection_one_notice_and_one_index`: resolver spy chama original; retry real do gateway e rodada de leitura preservam seleção/credencial/índice, aviso único.
  - `test_auto_capability_closes_on_cancel_or_lost_ownership`: reutilizar padrões de cancelamento/perda de posse existentes; leitor capturado recusa após fechamento.
- [x] **Verificar RED:** arquivos novos/alterados com `pytest -q`; registrar asserções ou construção dos novos contratos ausentes, sem aceitar falha de infraestrutura como RED.
- [x] **GREEN:** em `_stream_owned`, resolver seleção uma vez para on/auto. Auto inapto pula antes de qualquer consulta/handler de catálogo. Modelo apto exige handler nativo; prepara snapshot/capacidade com `allow_empty` só para auto. Derivar envelope efetivo com auto=False e on igual à capacidade real; passar a mesma seleção ao loop. ContextVars efêmeros e reset/finally atuais preservam isolamento.
- [x] **Aviso e router:** emitir ready/skip no ponto de aviso depois de start, sem duplicar por retry; serializar skip com motivo validado. Runtime rejeita novo campo mesmo em dispatch direto. Off não consulta nem anuncia catálogo.
- [x] **Verificar:** novo arquivo, `tests/test_interaction_skill_catalog.py`, `tests/test_interaction_contract.py`, `tests/test_interaction_service.py tests/test_interaction_router.py tests/test_interaction_retry.py tests/test_interaction_turn_ownership.py`; referências de assets, corrupção, concorrência e cancelamento existentes continuam passando. Ruff/format/diff-check.
- [x] **Commit:** `feat(chat): resolve catálogo automático dentro da posse do turno`.

### Task 3: CLI automática, interativo, compatibilidade e entrega

**Files:** modificar `kairos_cli/main.py`, `kairos_cli/chat.py`, `tests/test_cli_skill_catalog.py`; ampliar testes CLI/runtime e documentação pertinente.

**Interfaces:** parser `--skills-catalog` usa `BooleanOptionalAction`, default `None`; `run_chat(..., skills_catalog: bool | None = False)` e auxiliares aceitam None como auto, conservando default Python desligado. `_run_interactive` propaga o modo para cada `_run_turn`. Traduzir para os dois booleanos de T2; runtime auto vira off antes do envelope, on mantém recusa. `catalog_skip_reason` vira aviso fixo em stderr, pronto/pulado nunca entram no stdout NDJSON.

- [ ] **RED CLI real:** parametrizar `test_cli_auto_without_flag_exposes_only_index` para run/chat e humano/JSON; adapter local existente sem leitura deve receber índice mas nenhum corpo/referência. `test_cli_off_preserves_old_tool_history_without_catalog_io` usa `--no-skills-catalog`, confirma histórico antigo e zero capacidade/captura/consulta de catálogo. Atualizar teste antigo sem flag que agora representa comportamento intencionalmente alterado, preservando suas asserções no opt-out.
- [ ] **RED interativo:** `test_cli_interactive_propagates_mode_each_turn` parametriza auto/on/off com stdin de duas mensagens e saída; gateway real da fixture com duas respostas, nenhum prompt fictício one-shot. Índice fixo nos dois requests quando ativo; contexto de leitura fechado entre turnos e novo orçamento por turno, sem contar bundle.
- [ ] **RED compatibilidade:** `test_cli_auto_and_off_runtime_have_no_catalog_intent` observa envelope real do roteador/fixture runtime sem modelo pago; on recusa antes da composição. `test_cli_auto_empty_and_unsupported_report_only_stderr` mantém stdout JSON válido. `test_cli_catalog_flags_last_wins` verifica os dois pares de opções sem ler fonte. Flag não booleana continua recusada antes de I/O.
- [ ] **Verificar RED:** `.venv/bin/python -m pytest -q tests/test_cli_skill_catalog.py`; falhas ligadas ao padrão antigo/flag ausente e veto interativo antigo, não sandbox ou provider real.
- [ ] **GREEN:** parser e transportes propagam os modos; remover somente o veto de catálogo no interativo. Consumir novo aviso antes do renderer JSON. Preservar ferramentas gerais, workspace, `--allow-tool`, skills explícitas, idempotência e cancelamento de runtime.
- [ ] **Docs:** registrar padrão automático CLI, force/off, corpo sob demanda, snapshot desde primeira ativação positiva e histórico que permanece após opt-out. Atualizar estado do desenho para aprovado/implementado somente conforme evidência; registrar fonte RF-09/RF-17 sem comentários novos de rastreio em runtime.
- [ ] **Vertical:** famílias de catálogo, CLI Chat, router/protocolo e ferramentas, incluindo opt-out, overrides, Web/gateways e concorrência; pytest real, Ruff global, formatação global, diff-check e lock. Não executar benchmark do ledger nem provider pago.
- [ ] **Commit:** `feat(cli): ativa índice de skills por padrão no chat`.
- [ ] **Integração final:** `env -u SONAR_TOKEN bash scripts/ci.sh` com Python/Codex pinados, Docker/opt-ins pertinentes e frontends disponíveis. Conferir todos os resultados/skips, não apenas o final do log; Sonar remoto precisa atestar o SHA efetivo. Reusar evidência somente quando fonte/configuração/deps/ambiente pertinentes permanecerem iguais.
- [ ] **Revisão e entrega:** revisor independente capacitado do diff completo, Root verifica integração e resolve achados com RED/GREEN. Atualizar o PR #119 para o escopo final, manter rascunho, publicar dados de validação e conferir CI/Sonar. Não integrar/implantar por inferência da autorização antiga do #117.

## Auto-revisão

Cobertura: T1 define ausência tipada e preserva a captura estrita; T2 concentra decisão, posse, persistência e avisos; T3 ativa a CLI/interativo e verifica superfícies excluídas. Os cinco Review Focus têm testes nomeados na tarefa proprietária. Função opcional de T1 e parâmetros/campos de T2 são os mesmos consumidos em T3. Schema, manifesto e limites não mudam. Nenhum passo depende de cache negativo, exceção por texto ou autorização de ferramentas gerais.

Execução nativa e sequencial autorizada na conversa. Plano disponível para leitura durante a execução; mudanças que ultrapassem o desenho aprovado exigem resolver o novo escopo antes de implementar.
