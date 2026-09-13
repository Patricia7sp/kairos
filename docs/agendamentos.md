# Agendamentos

O Kairos executa instruções pelo modelo padrão em horários definidos. Cada
execução tem uma conversa própria, disponível no histórico de sessões. A tela
**Agendamentos** permite criar, pausar, retomar, excluir e consultar execuções.

O serviço Web verifica os jobs a cada 60 segundos. Precisa permanecer ativo;
`kairos tick` e `kairos cron tick` executam uma verificação avulsa na CLI. Dois
processos apontando para o mesmo `KAIROS_HOME` não executam o mesmo tick em paralelo.
Não há jobs criados automaticamente pela instalação ou migração.

```bash
kairos cron create --name 'Resumo periódico' --prompt 'Escreva um resumo breve' --every 60 --times 3
kairos cron list --json
kairos cron pause ID
kairos cron resume ID
kairos cron history ID --json
kairos cron remove ID
```

Na criação, use apenas uma opção de agenda:

- `--at '2026-10-01T09:00:00-03:00'`: uma execução, data ISO com fuso explícito.
- `--every 60`: intervalo em minutos a partir da criação.
- `--expr '0 9 * * *'`: expressão cron de cinco campos em UTC.

A interface recebe uma data/hora local e envia o instante com fuso. As expressões
cron são sempre UTC. `croniter`, antes opcional, agora é instalado junto do Kairos.
Sem limite, trabalhos recorrentes repetem até serem pausados/excluídos. Para limitar,
use `--times N` na CLI ou o campo opcional **Limite de ocorrências (opcional)** na tela, com um
inteiro de 1 a 1.000.000. `once` continua com no máximo uma ocorrência.

O limite conta ocorrências reservadas antes do envio ao modelo: sucesso, falha e
resultado desconhecido consomem uma ocorrência cada. Ao atingir o limite, a agenda
encerra; pausar/retomar não renova o limite. Repetições HTTP internas do turno não
criam outra ocorrência. Isso limita disparos, não gastos financeiros ou tokens.
A tela mostra o consumo e a recorrência ilimitada quando aplicável.

As execuções usam as preferências globais vigentes no início de cada turno e podem
consumir créditos. Este fluxo gera respostas de modelo; execução de ferramentas
continua na superfície Agent Runtime. O histórico distingue sucesso, falha e
resultado desconhecido. Falha de autenticação, política do provedor, timeout ou
ausência de evento terminal de sucesso nunca são apresentados como conclusão.

Pausar ou excluir impede próximos disparos; um turno já iniciado pode terminar.
Excluir o job preserva seu histórico e as conversas. A CLI pode consultar o histórico
mesmo após a exclusão. Se o serviço ficar desligado, o backlog colapsa para um único
disparo por job. Cada turno tem prazo de 300 segundos, seguido do fechamento do stream.

As definições ficam em `cron/jobs.json`; execuções na tabela `executions` de
`state.db`, introduzida pela migração v3. O ledger registra a ocorrência antes de
avançar o JSON e antes de enviar ao provedor. A unicidade `(job_id, scheduled_at)`
impede repetição se o processo morrer entre as duas gravações. Esse caso pode
consumir uma ocorrência sem gerar resposta; será registrado como resultado
desconhecido, sem tentar novamente automaticamente. O consumo é reconciliado com
o ledger antes de novas reservas, inclusive após reiniciar o serviço; JSON atrasado
não permite ultrapassar o limite. Ocorrência duplicada não consome duas vezes.

A API de criação aceita `times` inteiro opcional (null ou ausente para recorrentes
ilimitados). Jobs existentes permanecem como foram criados; não há edição de limite
de um job existente. `repeat.completed` no formato persistido é o contador de
ocorrências consumidas, apesar do nome histórico; não é contagem de sucessos.

A posse exclusiva do arquivo `cron/tick.lock` dura até o encerramento do stream.
Ela também permite reconciliar execuções abandonadas sem confundir PIDs entre
containers que compartilham o volume. A implantação atual usa Linux e `flock`;
o ticker em Windows ainda não foi validado. Não remova arquivos de lock ativos.

## Monitores de fonte

Um agendamento recorrente pode ter um **monitor de fonte**: o agente só roda
quando a fonte muda. A fonte é um comando executado a cada tick devido, e o
resultado é comparado com a última saída:

- **Primeira verificação ou mudança** → o agente roda como num tick comum, e o
  novo hash da saída é gravado junto com o claim (uma única reescrita do JSON).
- **Saída igual** → o tick é **suprimido**: o agente não roda, nenhuma ocorrência
  é reservada e nenhuma linha existe no ledger. A cadência avança para o próximo
  horário e o evento `cron.no_change` é registrado.
- **Fonte falhou** (tempo excedido, saída não nula, executável ausente) → **erro,
  nunca mudança**. O hash anterior fica intocado, a fonte não dispara o agente e
  o evento `cron.monitor_error` é registrado. Um erro não rebaixa a decisão para
  "nada mudou": continua sendo reportado como erro de fonte.

```bash
# Definir/consultar/remover a fonte de um job na CLI:
kairos cron create --name 'Vigiado' --prompt 'Aja a partir da fonte' --every 60 \
  --monitor '/opt/kairos/bin/check-status'
kairos cron monitor-show ID --json
kairos cron monitor-run ID      # executa a fonte uma vez, sem tocar em agenda
kairos cron monitor-clear ID

# O grupo dedicado:
kairos monitoring list
kairos monitoring status
kairos monitoring test ID
```

A execução da fonte é deliberadamente estreita:

- **Sem shell** (`shell=False`): a linha é dividida por `shlex.split`. `|`, `;`,
  `&&` são argumentos literais, não operadores. Use o caminho completo do
  executável e argumentos separados.
- **Ambiente mínimo**: só `HOME` (=KAIROS_HOME), `PATH`, `LANG`, `LC_ALL` e
  `TZ`. `KAIROS_HOME`, `KAIROS_WEB_TOKEN` e a passphrase do cofre **não** são
  passados ao script.
- **Orçamentos**: prazo padrão 30 s, máximo 120 s; saída padrão 64 KiB, máximo
  256 KiB. No timeout, o grupo de processos é morto por inteiro.
- **Sem dispatcher**: a fonte não invoca ferramentas nem envia mensagens. Falha e
  sucesso são determinados apenas pelo código de saída e pela saída capturada.

O campo persistido é `monitor: {"type": "script", "script": "<comando>"}`. O
estado fica em `monitor_state: {last_output_hash, last_changed_at,
last_checked_at}`. Só `script` existe hoje; `once` com monitor é recusado na
criação. Suprimir/encontrar erro não consome o limite de ocorrências — o orçamento
remanescente é o mesmo após um tick suprimido. Pausar um job evita até a execução
da fonte. Habilitar o monitor de um job existente redefine o estado: o próximo
tick devido é tratado como primeira verificação.

A API Web expõe `GET/PUT/DELETE /api/cron/jobs/{id}/monitor` e
`POST /api/cron/jobs/{id}/monitor/run` (teste avulso da fonte); o painel mostra a
fonte, a última verificação e a última mudança de cada job monitorado.

## Bloco de notas persistente por job

Cada job tem um **notepad**: um KV durável que sobrevive entre execuções
agendadas, usado para cursors, watermarks e listas de vigilância. A escrita é
feita pela CLI; o conteúdo é **injetado no final do prompt** antes de cada
execução do job, como um bloco no formato:

```text
## Job notepad (persistent across runs)
This durable scratchpad survives between scheduled runs of this job. Update it via the CLI, e.g.:
`kairos cron notepad ID set <key> <value>` (also: get/delete/list;
`kairos cron notepad ID delete <key>` removes an entry).

- cursor: 42
```

```bash
kairos cron notepad ID list              # lista as anotações do job
kairos cron notepad ID get cursor        # valor de uma chave (ou "null")
kairos cron notepad ID set cursor 42     # cria/atualiza uma chave
kairos cron notepad ID delete cursor     # remove uma chave
```

O notepad é chaveado por id do job, mas **não exige que o job exista** no momento
da escrita: o bloco acompanha o id e pode ser usado como memória de apoio mesmo
em fluxos avulsos. Notepad vazio injeta **string vazia** — jobs que nunca usam a
funcionalidade mantêm o prompt byte-idêntico. Excluir o job limpa seu notepad de
forma best-effort (a exclusão nunca é bloqueada por isso).

Limites documentados, com rejeição sem escrita parcial:

- `MAX_KEY_CHARS`: 128 caracteres por chave.
- `MAX_VALUE_BYTES`: 16 KiB por valor, medido em bytes UTF-8.
- `MAX_JOB_TOTAL_BYTES`: 64 KiB somando chave+valor de todas as chaves do job.

Os dados ficam na tabela `cron_notepad` de `state.db` (migração v4). A API Web
expõe `GET /api/cron/jobs/{id}/notepad`, `GET/PUT/DELETE
/api/cron/jobs/{id}/notepad/{key}` e `DELETE /api/cron/jobs/{id}/notepad`
(limpar tudo); o painel mostra o bloco de notas de cada job e permite salvar e
remover anotações.

## Guard do ciclo de vida do gateway

Na **criação** (nunca na execução) o guard rejeita jobs cuja **instrução** ou
**script de monitor** contém comando no formato que reiniciaria o próprio
processo que os executa — `kairos (gateway) restart|stop`, `hermes gateway
restart|stop`, `launchctl`/`systemctl` contra uma unit do gateway, `p?kill`/
`killall` contra o processo, `docker restart|stop` contra um container kairos.
É imposto em `JobStore.create` e `JobStore.set_monitor`, o que cobre toda
superfície de escrita: CLI `kairos cron create`/`monitor-set` e API Web
`POST /api/cron/jobs`/`PUT /api/cron/jobs/{id}/monitor`.

O padrão é **command-shaped**: ancora num identificador de comando concreto,
então **não dispara em prosa**. Um prompt que só cita comportamento de gateway
em inglês (ex.: "Kong API gateway restart behavior") ou comandos de
diagnóstico em data sink (`grep 'systemctl restart kairos' /var/log/syslog`,
`sqlite3 … LIKE '%pkill -f kairos%'`) é aceito; `grep … | sh` continua barrado.
Também colapsa continuações de linha POSIX (`\` + nova linha) antes de
comparar.

É **política de entrada**: a releitura do arquivo de jobs não re-roda o guard
(um job salvo antes de um endurecimento do padrão não pode tornar o documento
inteiro ilegível no boot). A recusa é um `ValueError` (`LifecycleGuardError`):
a CLI imprime em vermelho e sai 1; a API responde `422` com a justificativa.

Blueprints, schedulers externos e entrega para canais não estão ligados a este
executor. Campos sem implementação são recusados pela API.
