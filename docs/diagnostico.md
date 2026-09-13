# Diagnóstico local

`kairos debug` observa a instalação selecionada por `KAIROS_HOME`. Use
`kairos debug --json` para obter os mesmos resultados estruturados.

O comando verifica configuração, provedor reconhecido, modelo selecionável no
catálogo local, versão do schema e presença das tabelas do Chat, disponibilidade
do diário operacional e resposta do runtime existente. O runtime recebe somente
`runtime.status`, com prazo de um segundo; nenhum serviço é iniciado. Esse prazo
não limita a duração das leituras do sistema de arquivos.

A seleção global usa a mesma interpretação do Chat: campos `provider` e `model`,
referência `model: provedor/modelo` ou objeto com provedor e modelo. Preferências
específicas de conversas não são consultadas. Dados inválidos do cache ou da resposta
do runtime deixam a fonte indisponível, sem incluir seu conteúdo no relatório.

| Resultado | Significado |
|---|---|
| `complete`, saída 0 | Todas as fontes locais foram observadas/configuradas; runtime pronto ou explicitamente desativado. |
| `incomplete`, saída 1 | Uma ou mais fontes estão ausentes, inválidas ou indisponíveis. Os outros componentes continuam sendo examinados. |

Um diagnóstico completo **não certifica autenticação, geração ou funcionamento
integral do Chat**. O catálogo pode usar dados curados ou cache; presença no
catálogo não prova disponibilidade remota. Runtime ou diário ausentes podem não
impedir o Chat. Não há varredura de integridade do banco, conferência completa
de suas colunas/índices nem leitura do histórico de conversas.

Credenciais não são inspecionadas e o catálogo não é atualizado. Não há chamada
a provedores, reparo ou migração. A abertura SQLite em modo de leitura pode criar
arquivos auxiliares WAL/SHM. Não são emitidos IDs de modelos, URLs, caminhos,
cabeçalhos, mensagens, credenciais ou exceções brutas; somente o ID de um provedor
reconhecido pode aparecer.

A seção `privacy` informa o **padrão da aplicação** OpenRouter, incluindo
`data_collection: "deny"`. Não é uma leitura da política da conta nem da política
efetiva de uma conversa. Essa proteção não é alterada pelo diagnóstico.

Para verificações complementares, `kairos model test PROVEDOR` testa conexão,
`kairos model list` consulta o catálogo local e `kairos model refresh PROVEDOR`
atualiza o catálogo. Esses comandos têm escopos próprios e não são executados
automaticamente pelo diagnóstico. Uma geração bem-sucedida precisa de aceite
separado com o modelo e as políticas escolhidos.
