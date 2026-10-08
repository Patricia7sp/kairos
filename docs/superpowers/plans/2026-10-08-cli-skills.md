# Skills explícitas na CLI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar `--skill NOME` em turnos únicos de `run` e `chat`, com conteúdo efetivamente recebido pelo adapter e histórico estável após reinício.

**Architecture:** A CLI captura skills instaladas em snapshots imutáveis antes da composição. O serviço compartilhado persiste o contexto em `api_content`, preservando `content` e as permissões atuais. O loader abre arquivos por descritores sem seguir links; o roteador recusa skills no Agent Runtime.

**Tech Stack:** Python 3.11, argparse, asyncio, SQLite, dataclasses, PyYAML existente, pytest e unittest existentes.

**Spec:** [Especificação aprovada](../specs/2026-10-07-cli-skills-design.md), aprovada em 2026-10-08. Base de código `52e0de0`; branch isolada `feat/cli-skills`, worktree `.worktrees/skills_runtime`.

## Global Constraints

- Sem dependência nova, migração de banco ou ferramenta core nova.
- Somente `<home>/skills/NOME/SKILL.md`, diretório imediato; kebab-case até 64 caracteres e nome coincidente com o frontmatter.
- Até oito nomes únicos, 64 KiB por arquivo, 128 KiB agregados; medir bytes UTF-8, recusar excesso sem truncar.
- Usar validação de leitura (`new_skill=False`), descrição até 60 caracteres e corpo não vazio; não impor semver ou ponto final de autoria a documentos antigos.
- Ordem de primeira ocorrência; duplicatas da seleção não duplicam conteúdo. Sem seleção, nenhuma leitura do catálogo nem mudança no payload.
- Snapshots imutáveis; zero releitura durante tools, retry ou reconstrução de histórico. Omissão da opção não apaga mensagens antigas.
- Abrir abaixo do home canônico com `dir_fd`, `O_NOFOLLOW`, `O_DIRECTORY` para diretórios e `O_NONBLOCK` para arquivo; exigir arquivo regular com `st_nlink == 1`.
- `run`/`chat` com mensagem única; Agent Runtime e conversa interativa recusam a opção antes da composição. Skills não exigem `--tools`.
- Corpo não aparece nos avisos, erros ou logs operacionais; testes e verificação instalada usam só dados fictícios, sem `runtime_live`.
- Preservar workspace, aprovação, schemas, bash Docker, seleção de credenciais, sync, quarentena e proveniência.
- Commits/PR em português; merge somente após CI local completo e nove checks remotos verdes; deploy pós-merge da main, autorização já existente.

## Review Focus

- Um frontmatter com tipos inválidos não deve ganhar validade por coerção para texto — testar na tarefa 1.
- Um arquivo especial ou alterado durante a leitura não deve bloquear a CLI nem produzir um snapshot parcial — testar na tarefa 1.
- Trocar um caminho depois da abertura não deve redirecionar a leitura para outro arquivo — testar na tarefa 1.
- Uma falha na segunda skill não deve enviar ou persistir a primeira seleção parcialmente — testar na tarefa 3.
- Editar/apagar o arquivo e reiniciar o serviço não deve mudar a mensagem antiga enviada ao modelo — testar na tarefa 2.

## Arquivos e responsabilidades

| Arquivo | Responsabilidade |
|---|---|
| `kairos_skills/runtime.py` (novo) | Snapshot, validação da coleção, resolução e leitura limitada por descritores |
| `kairos_skills/frontmatter.py` | Modo estrito opcional do parser, mantendo o comportamento padrão |
| `kairos_integration/skill_context.py` (novo) | Serializar contexto e metadata, sem I/O |
| `kairos_integration/interaction_contract.py` | Campo `skills` e congelamento/validação na fronteira |
| `kairos_integration/interaction_service.py` | Usar contexto e metadata em `_persist_user`, sem alterar o loop do adapter |
| `kairos_integration/router.py` | Recusar skills no envelope de Agent Runtime |
| `kairos_cli/main.py`, `handlers.py`, `chat.py` | Opção, forwarding, validação antecipada, captura e aviso |
| `tests/test_skill_runtime.py` (novo) | Arquivos reais, formato, limites e contenção |
| `tests/test_interaction_skills.py` (novo) | Request real, persistência, retry, reinício e isolamento |
| `tests/test_cli_skills.py` (novo) | Entrada pública da CLI e guardas com adapter controlado |
| `tests/test_interaction_contract.py`, `test_interaction_router.py`, `test_skills.py` | Regressões dos contratos compartilhados |
| Guia CLI, decisões, registro de progresso e `kairos.egg-info/SOURCES.txt` | Uso, rastreabilidade e arquivos novos do pacote |

Sem refatoração ampla dos módulos grandes ou dos fixtures existentes. Reutilizar `RoundGateway`, `answer_round`, `make_service`, `collect` de `test_chat_search_loop` e `mutator_round` de `test_chat_tool_approval`. O fixture `install` de `test_cli_tools` permite chamadas reais com arquivos temporários; testes de resposta direta e reinício podem compor seus próprios serviços.

## Tarefa 1: Snapshot e leitura segura de skills instaladas

**Files:** criar `kairos_skills/runtime.py` e `tests/test_skill_runtime.py`; modificar `kairos_skills/frontmatter.py` e `tests/test_skills.py`.

**Interfaces:**
- `parse_frontmatter(text: str, *, strict_types: bool = False) -> tuple[Frontmatter, str]`: no modo estrito, recusar tipos inválidos antes de coerção. Campos escalares conhecidos exigem strings quando presentes; `author`/`license` podem ser ausentes ou `None`; `metadata` e seu namespace, se presentes, exigem mapas; listas conhecidas exigem listas de strings. O default preserva os consumidores atuais.
- `SkillSelectionError(ValueError)`: erro público com mensagem segura, sem conteúdo original ou caminho absoluto. Erros de parsing/OS são traduzidos sem traceback público.
- `SkillSnapshot(name: str, text: str)`: dataclass frozen; campos calculados `description`, `version`, `sha256` como strings `init=False`. Construção valida nome, tamanho, UTF-8 encodável, frontmatter estrito e corpo; extrai metadata e calcula SHA-256 do texto UTF-8. Não aceitar digest ou descrição fornecidos separadamente pelo chamador.
- `validate_skill_snapshots(skills: Sequence[SkillSnapshot]) -> tuple[SkillSnapshot, ...]`: copiar, exigir objetos do tipo, nomes únicos e limites da coleção. Diferente do loader, duplicatas de snapshots na fronteira são inválidas.
- `load_selected_skills(home: Path, names: Sequence[str]) -> tuple[SkillSnapshot, ...]`: copiar nomes, validar sintaxe completa, deduplicar em ordem, aplicar limites, ler e validar todo o conjunto. Retornar `()` antes de abrir qualquer diretório quando a seleção estiver vazia.
- Constantes públicas em `runtime.py`: `MAX_SELECTED_SKILLS = 8`, `MAX_SKILL_BYTES = 64 * 1024`, `MAX_SELECTED_SKILL_BYTES = 128 * 1024`.

- [ ] **1. Escrever testes RED do snapshot e loader.** Usar uma função local de fixture que grava frontmatter válido e corpo em arquivos reais, sem ler `.py`. Incluir estes contratos:

```python
# test_selecao_preserva_ordem_deduplica_e_hasheia_bytes
assert [s.name for s in loaded] == ["segunda", "primeira"]
assert loaded[0].text == original_text
assert loaded[0].sha256 == hashlib.sha256(original_text.encode("utf-8")).hexdigest()
# test_snapshot_nao_permite_mutacao: atribuir snapshot.text levanta FrozenInstanceError
# test_sem_selecao_nao_abre_catalogo: load_selected_skills(home, ()) == (); os.open falharia se chamado
```

Parametrizar `test_frontmatter_estrito_recusa_tipos_sem_vazar_conteudo` para inteiro em nome/descrição/versão, metadata escalar, listas em formato escalar, autor inválido e YAML malformado. Assertar `SkillSelectionError`, marcador fictício ausente do erro; validar também versão antiga e descrição sem ponto final aceitas. Testar nome do diretório divergente, vazio, traversal, absoluto, glob, newline, corpo vazio, UTF-8 inválido e descrição acima de 60 caracteres.

Testes `test_limites_em_bytes_sem_truncamento`: montar arquivos de tamanho exato 65.536 e 65.537 bytes, inclusive conteúdo multibyte; primeiro passa, segundo recusa. Total de 131.072 passa, 131.073 recusa. Oito nomes únicos passam, nove recusam; repetição do mesmo nome não consome limite extra.

Testes de I/O: `test_links_e_arquivos_especiais_recusados` com symlink em `skills`, no diretório e no arquivo, hardlink e FIFO real; executar FIFO em subprocesso com timeout de 2 segundos, sem leitor/escritor externo. `test_substituicao_apos_abertura_mantem_arquivo_original` intercepta a chamada real de `os.open`, renomeia o arquivo aberto para um nome irmão (preservando sua única ligação) e põe um symlink externo em `SKILL.md` depois de receber o FD; confirma texto original, não texto externo. `test_mudanca_no_descritor_recusada` altera o arquivo aberto entre leituras reais e confirma recusa, fechamento dos descritores e ausência de retorno parcial.

- [ ] **2. Executar RED.** `uv run pytest -q tests/test_skill_runtime.py tests/test_skills.py`; observar falhas dos novos contratos, mantendo registrada a baseline do parser existente.
- [ ] **3. Implementar as interfaces.** Ler `MAX_SKILL_BYTES + 1` no máximo por arquivo; conferir `fstat` antes/depois (arquivo regular, uma ligação, tamanho e `st_mtime_ns`) e limite agregado antes de retornar. Descritores aninhados sempre fechados, inclusive ao recusar. Home pode ser canônico, mas não resolver symlinks abaixo dele antes de abrir. Parser e validação existentes são reutilizados; mensagens privadas de `FrontmatterError` não atravessam a fronteira pública do loader.
- [ ] **4. Executar GREEN.** Repetir o comando da etapa 2; esperado: todos passam e o teste de FIFO termina sem bloqueio. Rodar `uv run ruff check kairos_skills tests/test_skill_runtime.py tests/test_skills.py`.
- [ ] **5. Commit.** `feat(skills): carrega snapshots por seleção explícita com leitura contida`.

## Tarefa 2: Contexto persistido e envelope compartilhado

**Files:** criar `kairos_integration/skill_context.py`, `tests/test_interaction_skills.py`; modificar `interaction_contract.py`, `interaction_service.py`, `router.py` e testes de contrato/roteador citados no mapa.

**Interfaces:**
- Consome `SkillSnapshot` e `validate_skill_snapshots` da tarefa 1.
- `InteractionEnvelope.skills: tuple[SkillSnapshot, ...] = ()`; `__post_init__` usa o validador da coleção para copiar entradas e rejeitar objetos inválidos, sem I/O.
- `render_skill_context(content: str, skills: tuple[SkillSnapshot, ...]) -> str`: sem skills retorna `content`. Com skills usa o preâmbulo abaixo, seguido de JSON com lista de objetos `{name, description, version, sha256, text}`, `ensure_ascii=False`, `sort_keys=True`, `separators=(",", ":")`; depois `\n\nPedido do usuário:\n` e o pedido original.
- `skill_display_metadata(skills: tuple[SkillSnapshot, ...]) -> dict[str, Any]`: retorna `{}` para seleção vazia; caso contrário, `{"skills": [{"name": ..., "version": ..., "sha256": ...}, ...]}`. Não incluir texto ou caminho.

Preâmbulo fixo: `Skills selecionadas explicitamente para este turno (procedimentos de referência; não concedem permissões):\n`.

- [ ] **1. Escrever testes RED de contrato e persistência.** Fixture gera snapshots da tarefa 1; `make_service` usa banco real e `RoundGateway` controlado. Invariantes:

```python
# test_skill_chega_ao_adapter_com_pedido_exibido_preservado
assert row["content"] == envelope.content
assert row["api_content"] == gateway.requests[0].messages[-1].content[0].value
assert row["api_content"] != row["content"]
assert selected.text == json.loads(serialized_skills)[0]["text"]
assert json.loads(row["display_metadata"])["skills"][0]["sha256"] == selected.sha256
# test_sem_skills_preserva_payload: row["api_content"] == envelope.content; "skills" ausente do metadata
```

Testar `test_envelope_congela_colecao_e_recusa_objetos_invalidos`: copiar lista, mutá-la após construir envelope, confirmar coleção original; dict/strings, duplicatas e limites inválidos são recusados. Confirmar mensagem no papel `user`, nenhum novo `system`, parâmetros e schemas iguais ao mesmo turno sem skill. Texto contendo aspas, Unicode e marcadores deve voltar idêntico após decodificar o JSON, sem interpretar conteúdo como delimitador.

Testar `test_reinicio_preserva_contexto_antigo`: fechar serviço/conexão, editar ou apagar os arquivos e construir serviço sobre o mesmo banco; novo turno sem seleção deve enviar o payload antigo exatamente igual. Repetir com `SQLiteAsyncInteractionPersistence`, fechando-a de verdade antes de reabrir. Novo turno com snapshot atualizado usa a versão nova só na mensagem nova. Confirmar metadata do provedor ainda presente e ausência de corpo no metadata de skills.

Testar `test_retry_e_rodadas_reusam_contexto_congelado`: primeira tentativa com erro retryable e depois sucesso, e turno de ferramenta com duas rodadas; alterar arquivo entre tentativas/rodadas. Comparar a mensagem user correspondente nos requests, os hashes, schemas e preparação de credencial única. Usar padrões de retry já existentes, sem chamadas de rede.

Testar `test_sessoes_concorrentes_nao_compartilham_contexto`: dois serviços/bancos e envelopes com textos diferentes, coordenados por eventos async; cada gateway vê apenas sua seleção nova. Mesma sessão conserva histórico conforme contrato. `test_runtime_recusa_skills_sem_submit` usa `make_router`/`FakeRuntimeClient` existentes: erro `invalid_event`, `submitted == []`, nenhum fallback para modelo.

- [ ] **2. Executar RED.** `uv run pytest -q tests/test_interaction_skills.py tests/test_interaction_contract.py tests/test_interaction_router.py`; esperado: novos testes falham antes do campo/renderização existirem.
- [ ] **3. Implementar contexto e persistência.** Alterar apenas kwargs de `_persist_user`: `api_content` renderizado e metadata adicional quando houver skills, nos dois caminhos sync/async. Usar `_history` existente, sem injetar mensagens virtuais a cada rodada ou reabrir arquivos. Adicionar `envelope.skills` à recusa do roteador de runtime; não mudar protocolo de RuntimeClient.
- [ ] **4. Executar GREEN e regressões do serviço.** `uv run pytest -q tests/test_interaction_skills.py tests/test_interaction_contract.py tests/test_interaction_router.py tests/test_interaction_persistence.py tests/test_interaction_async_persistence.py tests/test_interaction_retry.py tests/test_interaction_reload.py tests/test_chat_search_loop.py`; esperado: todos passam. Rodar ruff dos arquivos alterados.
- [ ] **5. Commit.** `feat(chat): preserva contexto de skills selecionadas no histórico do turno`.

## Tarefa 3: Entrada pública da CLI, permissões e entrega

**Files:** modificar `kairos_cli/main.py`, `handlers.py`, `chat.py`; criar `tests/test_cli_skills.py`; atualizar `docs/guia-cli-comandos.md`, `docs/decisoes.md`, `docs/cli-auth-progress.md`, `kairos.egg-info/SOURCES.txt`.

**Interfaces:**
- Consome `load_selected_skills` e `SkillSnapshot` da tarefa 1 e `InteractionEnvelope.skills` da tarefa 2.
- Parser de `run`/`chat`: `--skill`, `action="append"`, `default=[]`, `metavar="NOME"`. `cmd_run` encaminha `skill_names=getattr(args, "skill", ())`.
- `run_chat(..., skill_names: tuple[str, ...] | list[str] = ()) -> int`: copiar lista antes de compor; validar seleção somente em mensagem única e modo modelo; converter `SkillSelectionError` em `ChatUsageError` seguro. Carregar antes de `build_interaction_service`.
- `_run_turn(..., skills: tuple[SkillSnapshot, ...] = ()) -> int`: preencher envelope; sem forwarding para `_run_interactive`, pois a combinação é recusada.
- Aviso de sucesso em stderr: `Skills selecionadas: NOME1, NOME2.`; não imprimir corpos. Emitir após todas serem carregadas, uma única vez na CLI.

- [ ] **1. Escrever testes RED da CLI.** Usar `main`, banco real, skills temporárias e adapter controlado. Parametrizar `run`/`chat`, texto/JSON e stdin sem TTY; provar o recebimento efetivo:

```python
# test_cli_entrega_skills_selecionadas_ao_adapter
assert main(options) == 0
assert selected_text == json.loads(serialized_skills)[0]["text"]
assert stdin.tell() == 0
assert "Skills selecionadas: revisar-docs." in stderr
assert selected_text not in stderr
# JSON: cada linha de stdout decodifica e o último evento é turn_end
```

Testar seleção repetida/ordenada, `--skill` sem `--tools`, ausência de seleção sem chamar loader, e mutação da lista do chamador durante build sem mudar o envelope. `test_segunda_skill_invalida_nao_compoe_ou_persiste` confirma código 2, composição não chamada, zero mensagens user no banco pré-existente; parametrizar também erro de nome/arquivo, sem fallback para outro home ou bundle. `test_skill_sem_mensagem_ou_no_runtime_recusa_antes_de_compor` observa o mesmo erro antecipado.

Testar `test_mesmo_nome_em_homes_distintos_nao_vaza` em dois homes temporários. Adapters verificam conteúdo e parâmetros, inclusive um provedor local fictício; nenhuma consulta externa. Corpos com marcador privado fictício e arquivo malformado não devem surgir nos erros/avisos/caplog.

Testes de permissões com chamadas reais: `test_skill_nao_aprova_mutacao` sem TTY nega escrita e conserva stdin; `test_skill_com_allow_tool_escreve_apenas_dentro` confirma escrita interna e recusa absoluta, `..` e symlink para fora, preservando arquivo externo. `test_skill_nao_habilita_host_bash_mcp_git_agenda` usa chamadas controladas e observa ferramenta omitida/negada, nenhum marcador criado no host; comparar schemas com a execução sem skill. Não esconder o executor ou a guarda com mocks.

- [ ] **2. Executar RED.** `uv run pytest -q tests/test_cli_skills.py`; esperado: falhas da opção ou forwarding ausentes, sem executar provedor real.
- [ ] **3. Implementar CLI e documentação do comportamento.** Manter validações de `--allow-tool`, `--workspace`, idempotência e seleção de modelo. Documentar envio da skill ao provedor e persistência no histórico, limites, requisito de turno único e uso somente de `SKILL.md`. Registrar divergência de seleção explícita e o escopo ainda pendente de RF-09/RF-17. Atualizar o progresso dos PRs #92/#93 e deste recorte; acrescentar somente os arquivos novos a SOURCES, evitando mudanças geradas alheias.
- [ ] **4. Executar GREEN e regressões.** `uv run pytest -q tests/test_cli_skills.py tests/test_cli_chat.py tests/test_cli_tools.py tests/test_cli_automation.py tests/test_workspace_tools.py tests/test_chat_tool_approval.py tests/test_chat_sandbox.py tests/test_skills.py tests/test_active_credentials.py tests/test_credential_migration.py tests/test_cli_credentials.py`; esperado: todos passam. `uv run ruff check .`, `uv run ruff format --check .`, `git diff --check` e help de run/chat passam.
- [ ] **5. Commit.** `feat(cli): seleciona skills explicitamente para turnos do Chat`.

## Validação final e integração autorizada

- [ ] Executar `scripts/ci.sh` completo com Codex 0.154.0 no PATH; verificar imagens de teste necessárias, incluindo stub e worker offline. Anotar exit code, contagens observadas, skips/deselects e motivos; não tratar ausência de pré-requisitos como integração executada.
- [ ] Solicitar revisão independente do diff inteiro contra a main atual; corrigir achados com regressões antes da nova validação. Não delegar implementação sem a escolha do método pela usuária.
- [ ] Publicar branch/PR em português com problema, novo comportamento, limites e evidências. Aguardar os nove checks de verificação verdes; squash-merge com SHA do head validado, usando a autorização existente.
- [ ] Buscar main, conferir diff do squash e igualdade da árvore validada; acompanhar CI da main e job de deploy do Komodo até sucesso.
- [ ] Confirmar commit implantado, hashes dos módulos alterados, containers saudáveis e smoke. No código instalado, usar somente home/skills temporários e adapter fictício para verificar seleção, contexto renderizado, persistência e recusa de links; não selecionar skills ou credenciais reais de produção.
- [ ] Registrar PR, commit, execução de CI, logs e verificação instalada. Relatar RF-09/RF-17 como parciais e não anunciar autoria/curador/workflows como entregues.

## Auto-revisão do plano

Formato, leitura e limites → tarefa 1. Envelope, determinismo, metadata, reinício,
retry e isolamento → tarefa 2. Opções públicas, validação anterior à composição,
guardas e documentação → tarefa 3. Cada item de Review Focus tem um teste
nomeado na tarefa responsável. Interfaces de entrada/saída acima coincidem;
não há dependência circular de integration para skills nem migração de schema.

Status: plano preparado para revisão da usuária; tarefas ainda não executadas.
Método recomendado: execução pelo agente principal nesta sessão, seguida de
uma revisão independente do branch, porque os três blocos dependem dos mesmos
contratos e fixtures. A revisão deste plano e a escolha do método antecedem a
implementação.
