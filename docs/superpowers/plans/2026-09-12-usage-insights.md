# Métricas reais na CLI

Continuação do comando `insights`, já declarado como métricas de uso/custo.
Implementar consulta somente leitura compartilhada com `/api/analytics/usage`.
Não executar modelo, abrir cofre, exportar diagnósticos, nem inicializar/migrar DB.

## Contrato

- Extrair a agregação existente de `kairos_web/observability_api.py` para
  `kairos_state/usage_summary.py`. Público `read_usage_summary(home: Path,
  *, requested_days: int | None = None) -> dict`; conservar contrato JSON da API.
- Fonte é `session_model_usage`, acumulada por rota de faturamento. Somar contadores
  persistidos e valores de custo conhecidos, mantendo actual/estimated separados,
  `cost_status`, `unknown_cost_routes` e `cost_totals_complete`. Ausência de custos
  permanece None; subtotal conhecido não deve ser apresentado como custo total.
- Janela temporal/série diária não está disponível nessa fonte. Preservar contrato
  explícito all_time/window_supported=false/daily_status=unavailable da API; CLI
  não oferece flags de período que aparentem filtrar dados.
- `kairos insights [--json]`: handler real, retorno 0 em leitura válida (inclusive
  DB vazio com schema existente), 1 se indisponível/corrompido/schema ausente.
  Texto em português com uso acumulado e custos conhecido/estimado/incompleto.
  Saída não contém IDs de sessão, prompts, credenciais, endpoints, exceções nem paths.
- API passa a chamar o leitor compartilhado, preservando auth/isolamento e o
  parâmetro days já existente sem mudar seu significado explicitamente não suportado.

## Validação

1. TDD com DB real: múltiplas rotas/tokens/cache/raciocínio, custos zero e None,
   mistura actual/estimated/unknown, vazios, arquivo ausente/corrompido/schema antigo.
2. CLI e API JSON iguais; auth permanece, nenhuma criação/migração/modificação de
   arquivo de DB/config/cofre. Consumo gerado por turno real com HTTP controlado
   aparece no relatório após reabrir o banco.
3. Revisão independente, CI, imagem e consulta somente leitura na produção após
   entrega. Sem inventar séries ou declarar conclusão dos demais comandos.
