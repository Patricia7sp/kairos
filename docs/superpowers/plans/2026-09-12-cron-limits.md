# Limites de ocorrências para agendamentos recorrentes

Continuação autorizada do inventário funcional: permitir intervalo/cron finitos,
mantendo recorrência ilimitada como padrão atual. Não alterar jobs existentes nem
criar trabalhos na produção para aceite. Base inicial PR #26; atualizar após
integrar os registros operacionais antes da validação final.

## Contrato

- `JobStore.create(..., times: int | None = None)`; API aceita `times` estrito
  opcional; CLI `cron create --times N`. Interval/cron aceitam None ou inteiro
  1..1000000. `once` continua uma ocorrência; aceita None ou 1 e rejeita outros.
- UI oferece limite opcional em recorrentes e mostra reservadas/limite ou
  recorrência ilimitada. Formulário once não envia valor de campo oculto. Linguagem
  deixa claro que falhas e resultados desconhecidos também consomem ocorrência.
- Orçamento conta reservas duráveis, não respostas bem-sucedidas nem dinheiro.
  Repetições HTTP dentro de um turno continuam parte da mesma ocorrência. A
  proteção do modelo, seleção global e prazo do turno permanecem existentes.
- `repeat.times` guarda o limite; `repeat.completed` é compatível com o formato
  existente e passa a espelhar ocorrências consumidas, inclusive nos recorrentes.
  Ao esgotar, `next_run_at = None`; pausa/retomada não renova orçamento nem agenda.
- Fonte durável para reconciliar consumo: linhas de `executions` por job. Sob o
  lock existente, usar ao menos o maior entre contador JSON válido e COUNT do
  ledger, para não ampliar orçamento caso JSON esteja atrasado após crash. Insert
  único precede JSON; duplicata não consome duas vezes e nunca repete efeito.
- Backlog continua colapsado; contar apenas ocorrência efetivamente reservada.
  Não adicionar edição de limites de jobs existentes neste lote.

## Implementação e validação

1. Backend TDD: validação estrita, recorrente finito, erro/unknown consomem,
   sem terceiro efeito após limite dois, crash entre ledger/JSON, duplicata,
   pausa/retomada e reabertura; preservar once e antigos ilimitados.
2. API/CLI compartilham contrato; UI nativa com estado esgotado e limite opcional,
   testes de envio/validação e exibição, sem controles ocultos interferindo.
3. Navegador real em armazenamento descartável, scheduler real/HTTP controlado,
   ledger e histórico após reload; confirmar limite esgotado impede novo request.
4. Revisão independente, CI, imagem, atualização de manual/progresso e publicação
   somente após checks. Aceite não usa a conta nem os prompts da usuária.
