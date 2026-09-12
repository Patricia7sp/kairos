# Diário operacional dos serviços

## Escopo e critérios

Continuar pendências após o lote de Chat. A UI atual não usa `/api/env` nem
`/api/logs`; a primeira é contrato legado de configuração/segredos e a segunda é
um stub. A CLI declara `logs` sem handler. Implementar fonte real compartilhada,
leitura CLI/API e uma tela nativa; retirar explicitamente a rota legada env em
favor dos fluxos atuais de provedores/cofre. Não recriar editor de segredos.

Diário de eventos operacionais, separado de transcript e do stderr bruto. Catálogo
fixo de códigos produz serviço, nível e mensagem em português. Campos livres,
prompts, consultas, IDs de conversas, modelos arbitrários, URLs, headers, paths,
exceções e credenciais não entram no diário. Registros incluem timestamp UTC e
somente contadores inteiros aprovados entre zero e um bilhão.

Fonte: `<KAIROS_HOME>/logs/service-events.jsonl`, arquivo de até 512 KiB e uma
rotação `.1` igualmente limitada. Linhas JSON até 4 KiB. Escrita best effort e
serializada entre processos; erros do diário nunca derrubam o serviço. Arquivos
0600, diretório 0700 quando criados; rejeitar symlinks nos arquivos/diretório do
diário, usando descritores de diretório e O_NOFOLLOW. Leitura não cria diretórios,
cofre ou estado e não acessa nomes de arquivo fornecidos pelo cliente.

Leitor retorna estado `ready`, `unavailable` ou `error`, eventos recentes primeiro,
limite 1–200, filtros opcionais por serviço/nível. Ausência é indisponível; arquivo
válido vazio é vazio. Corrupção/tamanho inesperado/arquivo inseguro é erro explícito
sem exposição de linhas brutas. API autenticada e CLI compartilham contrato. Tela
mostra estados distintos, filtros, atualização e horário; escape de todo conteúdo.

## Catálogo inicial e interface do módulo independente

Módulo `kairos_observability/service_events.py` e `__init__.py`:

- `record_service_event(home: Path, code: str, **counters: int) -> bool`
- `read_service_events(home: Path, *, limit: int = 50, service: str | None = None,
  level: str | None = None) -> dict` contendo `state`, `events`, `source` constante
  `service-events`, e erro seguro quando necessário.
- Registro público: `timestamp` ISO UTC, `code`, `service`, `level`, `message`,
  `counters`. Na persistência, só código/timestamp/contadores; textos derivados.
- Códigos: `web.started`, `web.stopped`, `chat.completed`, `chat.failed`,
  `chat.cancelled`, `search.completed`, `search.failed`, `cron.completed`,
  `cron.failed`, `cron.unknown`. Serviços: web/chat/search/cron. Níveis: info para
  started/stopped/completed; warning para cancelled/unknown; error para failed.
- Contadores aprovados: `api_calls`, `input_tokens`, `output_tokens`, `results`.
  Campos desconhecidos ou valores inválidos rejeitam a gravação inteira.

Eventos `chat.*` descrevem uma chamada/rodada ao modelo, não o turno completo: uma
resposta com busca pode gerar dois `chat.completed` separados por `search.completed`.
O contador `api_calls` inclui as tentativas/retries dessa rodada. Produtores async
aguardam a gravação em thread, sem bloquear o event loop; cancelamento aguarda a
escrita reservada antes de propagar, sem tarefa órfã.

## Execução

1. TDD do módulo independente: persistência/reabertura, dois processos, limites,
   rotação, corrupção, symlinks, filtros, leitura sem efeitos e sentinelas secretas.
2. Atualizar branch após integrar Chat; ligar produtores a lifecycle Web, término
   de rodada/modelo, busca e execução cron usando apenas o catálogo aprovado.
3. Handler `kairos logs` com `--limit`, `--service`, `--level`, `--json`; API GET
   `/api/logs` real e env autenticado 410 com substituição por provedores/cofre.
4. Tela nativa de registros com testes DOM e aceite app real; revisão independente,
   CI e imagem, registro de limites reais no progresso funcional.

## Continuidade

Branch `feat/service-events`, worktree isolado, criado sobre `1b5e317`. O módulo
independente pode avançar enquanto Chat termina validação. Integrar a base de Chat
antes de editar produtores, sem alterar o outro worktree durante seu aceite.
