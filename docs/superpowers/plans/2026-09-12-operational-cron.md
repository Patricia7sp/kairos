# Cron operacional do Kairos

A usuária autorizou implementação, validação e entrega autônomas do escopo funcional.
Este lote preserva jobs em `cron/jobs.json`, ledger `executions` no SQLite e a
semântica de claim pré-efeito, pausa, backlog colapsado e recuperação por dono morto.
A identidade e a SPA nativa do Kairos continuam como superfícies públicas.

## Critérios de aceite

- [x] Criação e validação de jobs once/interval/cron; leitura persistida, pausa,
  retomada e exclusão sem perder histórico. Arquivo inválido falha sem sobrescrever.
- [x] Claim exclusivo entre processos antes do efeito, limite finito respeitado,
  ledger terminal imutável, recuperação de dono morto como unknown sem retry.
- [x] Tick usa o serviço de interação canônico em sessão própria; só registra
  completed após evento terminal de sucesso. Erros/timeout nunca viram sucesso.
- [x] Ticker integrado ao ciclo de vida do serviço Web, com desligamento limpo;
  nenhuma automação criada ou ativada pela migração.
- [x] CLI create/list/pause/resume/remove/history/status/tick e tick global funcionam.
- [x] API autenticada e tela nativa de agendamentos com criação, estados e histórico.
- [x] Testes reais de persistência/concorrência, turno com transporte controlado,
  CLI, API e navegador; revisão independente e CI local concluídas.
- [x] CI remota, integração e entrega da imagem após backup verificado. PR #25, merge `1b5e317`; aceite publicado registrado em `docs/functional-progress.md`.

Primeiro implementar o fluxo de prompts com saída no histórico do Kairos. Monitores,
notepad, blueprints e destinos externos terão aceite próprio antes de constar como
funcionais; o contrato não aceitará opções sem execução correspondente.
