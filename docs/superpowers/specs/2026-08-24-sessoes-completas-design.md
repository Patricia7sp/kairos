# Sessões completas — Design

## Objetivo

Tornar a página de Sessões uma biblioteca navegável e segura para o histórico do Kairos, preservando o transcript e suportando organização por tags, paginação, estados reversíveis e exportação.

## Decisões

- Tags serão persistidas em `session_tags(session_id, tag)` para não alterar a forma da tabela canônica `sessions` nem invalidar bancos existentes.
- A API continuará retornando sessões em ordem decrescente de `started_at`, com `limit` e `offset`, total real e `has_more`.
- Sessões ocultas não aparecem na listagem padrão; o filtro `ocultas` permite recuperá-las.
- Arquivar e ocultar são operações reversíveis. Nenhuma ação da nova interface executará `DELETE` físico.
- Exportação continua disponível em JSON e ganha Markdown para uma sessão selecionada.
- Tags são strings normalizadas (trim, minúsculas), entre 1 e 32 caracteres, no máximo 20 por sessão.

## Contratos

`GET /api/sessions?limit=50&offset=0&q=&status=todas&tag=` retorna:

```json
{
  "sessions": [{"id": "...", "tags": [], "archived": false, "hidden": false}],
  "total": 0,
  "offset": 0,
  "limit": 50,
  "has_more": false,
  "abertas": 0,
  "tag_counts": [{"tag": "projeto", "count": 2}]
}
```

`PATCH /api/sessions/{id}` aceita qualquer combinação de `archived`, `hidden`, `pinned` booleanos e `tags` como lista de strings. A atualização de tags substitui atomicamente o conjunto anterior; o transcript não é modificado.

## Interface

- Barra de busca, filtro de estado, filtro de tag e botão Atualizar.
- Rodapé da lista com anterior/próxima e “X–Y de Z”.
- Tags visíveis na linha e editáveis no detalhe.
- Botões Exportar JSON, Exportar Markdown, arquivar/desarquivar e ocultar/mostrar.
- Estados vazios, erros, carregamento e seleção por teclado preservados.

## Limites

- Não há exclusão permanente nesta etapa.
- Busca textual continua usando `LIKE` para preservar o comportamento atual; uma otimização FTS pode ser feita separadamente.
