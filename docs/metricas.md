# Métricas de uso e custo

`kairos insights` consulta a contabilidade persistida da instalação. Para consumir
o mesmo relatório da API em scripts, use `kairos insights --json`.

```bash
kairos insights
kairos insights --json
```

Use o `KAIROS_HOME` da instalação que deseja consultar. O comando lê o banco,
sem abrir o cofre, chamar modelos ou inicializar/migrar dados. Retorno 0 indica
leitura válida, inclusive banco vazio com schema existente; retorno 1 indica
contabilidade indisponível, como arquivo ausente, ilegível ou incompatível.

A conexão é somente leitura dos dados. O SQLite pode criar os arquivos auxiliares
`state.db-wal` e `state.db-shm` para coordenar o acesso ao banco. Transações
confirmadas que ainda estão no WAL entram no relatório, inclusive com a aplicação
em execução. Nenhum banco ausente é criado pela consulta.

O relatório mostra chamadas e tokens acumulados, incluindo os contadores
persistidos de cache e raciocínio. Custos informados e estimados são separados.
Quando há rotas sem custo conhecido, os valores disponíveis são subtotais
incompletos; custo desconhecido não vira zero. Uma quantia zero registrada
continua sendo zero conhecido. Valores numéricos inválidos ou somas não finitas
tornam a contabilidade indisponível, sem reparar ou alterar o banco.

A fonte agrega uso por rota de faturamento ao longo do tempo. Não há filtro por
dia nem série diária disponível nesse armazenamento. Por isso, o JSON informa
`period: all_time`, `window_supported: false` e `daily_status: unavailable`.
O parâmetro `days` já existente em `/api/analytics/usage` permanece apenas como
solicitação registrada no retorno: não filtra nem inventa dados temporais.

CLI e API usam o mesmo leitor. A rota continua autenticada. O relatório não inclui
prompts, credenciais, IDs de conversa ou endpoints; ele representa os dados que
o Kairos contabilizou, não uma fatura completa do provedor.
