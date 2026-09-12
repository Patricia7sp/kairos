# Agendamentos

O Kairos executa instruções pelo modelo padrão em horários definidos. Cada
execução tem uma conversa própria, disponível no histórico de sessões. A tela
**Agendamentos** permite criar, pausar, retomar, excluir e consultar execuções.

O serviço Web verifica os jobs a cada 60 segundos. Precisa permanecer ativo;
`kairos tick` e `kairos cron tick` executam uma verificação avulsa na CLI. Dois
processos apontando para o mesmo `KAIROS_HOME` não executam o mesmo tick em paralelo.
Não há jobs criados automaticamente pela instalação ou migração.

```bash
kairos cron create --name 'Resumo periódico' --prompt 'Escreva um resumo breve' --every 60
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
Os trabalhos recorrentes repetem até serem pausados/excluídos; `once` dispara no
máximo uma vez. Orçamentos finitos para recorrentes não são aceitos neste lote.

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
desconhecido, sem tentar novamente automaticamente.

A posse exclusiva do arquivo `cron/tick.lock` dura até o encerramento do stream.
Ela também permite reconciliar execuções abandonadas sem confundir PIDs entre
containers que compartilham o volume. A implantação atual usa Linux e `flock`;
o ticker em Windows ainda não foi validado. Não remova arquivos de lock ativos.

Monitores de fonte, notepad, blueprints, schedulers externos e entrega para canais
não estão ligados a este executor. Campos sem implementação são recusados pela API.
