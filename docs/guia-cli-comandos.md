# Como adicionar um comando à CLI do Kairos

A CLI do Kairos é a terceira camada de entrada — **Web ─ CLI ─ API → Core**. Ela
não reimplementa o agente: os comandos consomem as mesmas units que a Web usa
(`kairos_cli/chat.py` e `kairos_web/server.py` importam o mesmo
`build_interaction_router`; decisão D-PC.1). Um comando novo só **atravessa** a
árvore até uma unit existente; se a unit não existe, o esforço é na unit, não
na CLI.

## O contrato

- **Sem efeito real é pior que ausência.** Um comando que "reporta sucesso"
  sem fazer nada é bug. Comando pendente fica `NOT_IMPLEMENTED` e sai com 69 +
  mensagem do que falta (decisão D-CLI.9) — nunca com 0.
- **Comportamento real, não snapshot.** Testes afirmam contratos (ler estado
  persistido de verdade), não contagens congeladas.
- **Nada de lógica core na CLI.** Se o comando começou a duplicar o core,
  ele está no lugar errado: a lógica vai para a unit, o handler só a chama.

## Conversar com ferramentas

```bash
kairos chat --session trabalho --tools
kairos run --session trabalho --tools "Leia o README.md e resuma o projeto"
kairos run --session trabalho --tools --workspace ~/projetos/app "Edite o README.md"
kairos run --session pesquisa --tools --web-search "Pesquise o assunto e consulte as fontes"
```

`--tools` habilita as ferramentas disponíveis do Chat dentro do workspace.
A busca continua dependendo de `--web-search`.
Sem `--tools`, o comportamento anterior é preservado. Sessões do Agent Runtime
usam ferramentas nativas e recusam essa opção; suas aprovações continuam em
`kairos runtime approve`.

Cada chamada mutadora mostra nome e argumentos em stderr e exige `sim` no
terminal. Enter, resposta diferente, EOF ou expiração recusam a chamada. Não há
aprovação automática para pipes: stdin sem TTY e `--json` recusam mutações
imediatamente, sem consumir a entrada. Em JSON, stdout permanece NDJSON de
eventos; avisos ficam em stderr. Falha ou recusa de ferramenta faz o comando
terminar com código 1, mesmo quando o modelo produz uma resposta final.

O workspace é o diretório atual no início do comando, ou o diretório existente
informado por `--workspace` (exige `--tools`). Caminhos relativos usam essa raiz;
caminhos absolutos precisam ficar dentro dela. A aprovação de uma escrita não
autoriza sair do workspace. Leitura, escrita, edição, patch, busca e listagem
recusam links simbólicos e arquivos com múltiplos hard links, inclusive links
internos. Busca e listagem omitem esses itens. Um patch em lote valida todos os
arquivos antes de começar a escrever.

Shell no host, Git, agenda, servidores MCP de terceiros e overrides de plugins são
omitidos/recusados nesse modo: seus processos ou handlers não têm contenção de
arquivos comprovada. `bash` continua disponível com `chat.sandboxed_bash`: roda
no worker Docker isolado, com workspace próprio vazio, sem montar o projeto
do host. A raiz dos arquivos fica fixada por turno, incluindo aprovação e
tentativas, e não depende de mudanças posteriores no diretório do processo.

Essa fronteira cobre as operações de arquivo das ferramentas nativas. Plugins
instalados e hooks em processo continuam sendo código confiável; isso não é
isolamento de todo o processo Kairos. Web, canais e ACP mantêm suas políticas
existentes; o ACP continua solicitando aprovação para edições fora de seu cwd.

O conjunto de schemas é fixado no início do turno. Ferramentas indisponíveis ou
registradas depois não podem ser executadas por uma chamada inventada pelo
modelo; a seleção será refeita no próximo turno.

## Passo a passo

### Credenciais de provedores

```bash
kairos auth vault-status --json
kairos login --provider openai
kairos logout --provider openai
```

`login` pede a chave em um prompt oculto quando executado em terminal. A opção
`--api-key` continua disponível para compatibilidade. CLI e Web gravam pelo
mesmo serviço: o segredo fica no keyring ou no cofre criptografado; `auth.json`
contém referências e métodos de autenticação. O login configura a credencial,
sem fazer uma chamada de rede para verificar saldo ou disponibilidade.

O cofre precisa estar acessível ao processo. Com keyring disponível, não há
senha-mestra. Para o backend criptografado em processos separados, configure
`KAIROS_VAULT_PASSPHRASE_FILE` com um arquivo administrado do usuário atual, sem
permissões de grupo/outros (modo `0600`). `kairos auth vault-unlock` verifica a
senha somente naquela execução; não desbloqueia os comandos seguintes.

`logout --provider` remove todas as credenciais locais daquele provedor;
`logout` sem provedor remove todas as credenciais locais do perfil ativo. A
remoção pela página de Provedores continua limitada à credencial principal.
Fontes externas somente leitura recusam alterações. Cofre bloqueado ou
metadados inválidos produzem erro, sem anunciar sucesso. Credenciais legadas
devem ser migradas com `kairos auth migrate --confirm-remove-plaintext` antes
de um novo login.

As operações usam o lock compartilhado e tentam restaurar segredos, métodos e
metadados anteriores se uma etapa de persistência falhar. Isso não constitui
uma transação atômica entre os dois arquivos em caso de interrupção abrupta
do processo.

### Implementar um comando

1. **Declare na árvore** `kairos_cli/commands.py` — um `_c(...)`/`_sub(...)`
   com `help`, `status=Status.IMPLEMENTED` e `unit=` (a unit que sustenta o
   comando). A árvore é declarada como dados de propósito: a superfície inteira
   fica legível de uma vez e a contagem deixa de ser folclore.
2. **Se o comando é do Kairos** (não existe no legado): adicione o nome ao
   conjunto `PROPRIOS_DO_KAIROS` em `tests/test_cli_surface.py` — o teste de
   evidência conta 48 herdados do legado e os próprios à parte; apagar isso
   quebra a contagem como evidência.
3. **Argumentos específicos** em `kairos_cli/main.py` `_extra_args(...)` —
   posicionais e flags que só esse comando tem (há um dispatcher por
   `command`/`subcommand`; existe um toll de complexidade deliberado ali).
4. **Handler** em `kairos_cli/handlers.py`: função `cmd_<comando>(args)` que
   chama a unit (lazy import — o projeto corta ciclo entre camadas assim) e
   devolve `ExitCode` (`OK=0`, `ERROR=1`, `USAGE=2`, `NOT_IMPLEMENTED=69`).
   Registre em `HANDLERS`. Sem handler, `main` intercepta e recusa com 69.
5. **Testes.** A superfície e o comportamento:
   - `tests/test_cli_surface.py` valida ajuda, handler e contagens —
     rodo sempre que mudo a árvore.
   - Teste de comportamento real, no padrão dos existentes (ex.:
     `tests/test_cli_insights.py` lê `state.db` de verdade via
     `connect`+`initialize_schema`; `tests/test_cli_shell.py` injeta o
     `evaluate` do shell). Sem change-detector.
6. **Onde mora a interatividade.** Se o comando é o acolhimento do terminal,
   ele entra no menu do shell em `kairos_cli/shell.py` (`_menu`) — uma opção
   por linha, delegando ao `main` com o argv exato que o usuário digitaria.

## Validação obrigatória antes do merge

```bash
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
scripts/ci.sh --fast   # mesmo pipeline dos jobs do CI
```

O `test_superficie_totalmente_implementada_sem_pendencias` exige que todo
comando declarado esteja `IMPLEMENTED` com handler — não há "ado que existe na
árvore e não faz nada" aceitável.
