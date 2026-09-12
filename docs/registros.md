# Registros operacionais

Em **Configuração → Registros**, consulte eventos de Web, Chat, Busca e
Agendamentos. Os filtros escolhem serviço, nível e quantidade; **Atualizar** busca
os eventos mais recentes. Horários são exibidos em UTC.

O mesmo diário pode ser consultado pelo terminal:

```bash
kairos logs
kairos logs --service chat --level error --limit 20
kairos logs --json
```

Use o mesmo `KAIROS_HOME` da instalação que deseja observar. A leitura não cria
estado e não precisa abrir o cofre. Retorno 0 indica leitura válida, mesmo sem
eventos correspondentes; retorno 1 indica diário indisponível ou erro de leitura.
Argumentos inválidos são rejeitados pelo parser antes da leitura.

Cada evento do Chat descreve uma chamada ao modelo. Uma resposta com busca pode
produzir uma chamada inicial, um evento de busca e outra chamada para a resposta
final. Os contadores de chamadas incluem tentativas de repetição daquela rodada;
tokens só aparecem quando informados pelo provedor. Conclusão de uma chamada não
certifica a conclusão de todo o turno. Consulte a conversa para o resultado final
e o histórico de Agendamentos para o estado persistido de cada execução.

Os registros contêm apenas horário, tipo de evento e contadores aprovados. Não
incluem prompts, consultas de busca, respostas, identificadores de conversa,
URLs, credenciais ou detalhes de exceções. A proteção OpenRouter
`data_collection: "deny"` continua independente e permanece ativa por padrão.

O diário conserva até dois arquivos de 512 KiB em `logs/` dentro de `KAIROS_HOME`;
os eventos antigos saem na rotação. Gravações são best effort: contenção ou falha
de armazenamento pode perder eventos sem interromper as funcionalidades. O diário
não substitui o banco de conversas, a contabilidade nem o histórico de execuções.
Não há garantia de auditoria completa ou recuperação automática de arquivo
corrompido. Ausência, lista vazia e falha de leitura são estados distintos na tela.

Para integrações, `GET /api/logs` autenticado usa os mesmos filtros e retorna
`state: ready | unavailable | error`. O antigo `/api/env` foi aposentado com
HTTP 410; use as configurações de provedores e cofre existentes.
