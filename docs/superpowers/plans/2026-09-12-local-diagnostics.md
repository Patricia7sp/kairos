# Diagnóstico local de Chat, Modelos e Provedores

Continuação autorizada do inventário: ligar `kairos debug [--json]` a observações
locais reais. Complementa status/model/doctor; não chama doctor, pois ele migra
estado, nem replica upload do debug legado. Base PR #30, b47018d.

## Contrato

- Módulo `kairos_observability/diagnostics.py`, público async
  `read_diagnostics(home: Path) -> dict`; CLI sem dependência de FastAPI.
- Configuração e catálogo são os mesmos da composição canônica local. Provedor
  reconhecido pode ser identificado por seu ID do registry; nomes arbitrários,
  IDs de modelo, endpoints, cabeçalhos, prompts, paths e credenciais não saem.
  Modelo reporta ausência, indisponibilidade, presença e seletividade no catálogo,
  fontes conhecidas e capacidade de ferramentas. Sem atualizar o catálogo.
- Autenticação/geração são sempre `not_tested`, cofre `not_inspected`. Política
  OpenRouter publicada é explicitamente o padrão da aplicação, não configuração
  da conta nem política específica de uma conversa.
- Banco: existência, schema atual e tabelas necessárias ao Chat, modo journal.
  Não realizar integridade completa, migrations, contagem/leitura de mensagens.
  Como nos outros leitores SQLite, auxiliares WAL/SHM são permitidos.
- Diário: disponibilidade real pelo leitor limitado existente; não copiar logs
  brutos nem mensagens. Runtime: somente RPC runtime.status no socket existente,
  prazo de 1 segundo e projeção de state/enabled, sem iniciar serviços/processos.
- JSON `scope: local_only`, `state: complete | incomplete`. Complete significa
  fontes locais observadas/configuradas, nunca certificação de geração. Ausência
  de runtime ou diário pode não impedir o Chat, mas deixa o diagnóstico incompleto.
  CLI sai 0 para complete e 1 para incomplete, com explicação em português.
- Configuração inválida não impede sondar banco/diário/runtime. Erros são códigos
  fixos; exceções brutas não são emitidas. Nenhuma rede externa ou escrita de dados.

## Execução e aceite

1. TDD com configuração/catálogo/SQLite/diário reais e servidor Unix socket real;
   socket ausente, timeout, resposta inválida, cancelamento e isolamento de homes.
2. Regressões: banco ausente/corrompido/antigo, modelo desconhecido/não selecionável,
   configuração inválida, segredo sentinela em todos os arquivos, cofre nunca aberto,
   nenhum cliente HTTP instanciado, nenhuma geração e dados preservados.
3. CLI em processo novo, revisão independente, CI/imagem e consulta da instalação
   publicada. Registrar status atual dos três componentes e limites do diagnóstico.
