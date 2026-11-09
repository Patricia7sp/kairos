# E0.3 — coleta no Mac e diagnóstico do transporte

## Coleta real em 09/11/2026

Rodada `32ccec5835c84129bfd859b910838dca`, código `0860795d6cd644fbbcb8e04beb0dcc6df423a496`, navegador Chrome 156 / MacIntel, HTTPS real até o servidor Linux escolhido. O User-Agent declara uma versão de compatibilidade do macOS; não substitui a versão 26.7.2 informada pela usuária na matriz. A medição inclui rede e somente negações sintéticas, sem executar ferramentas nem usar provider pago.

| Cenário | Ne­gações válidas / tentativas | Confirmação persistida p95 (ms) | Limite superior evento–painel p95 (ms) |
| --- | ---: | ---: | ---: |
| Ocioso | 30 / 30 | 157,9 | 71,0 |
| Conversa/painel | 30 / 30 | 169,8 | 90,1 |
| Auditoria | 30 / 30 | 213,7 | 107,8 |
| Execução isolada | 30 / 30 | 155,1 | 87,2 |
| Recuperação | 30 / 30 | 166,8 | 79,3 |

As 150 tentativas foram conservadas; nenhuma foi timeout/erro ou falha de DOM. O Root recalculou os resumos a partir das brutas e conferiu igualdade. Os máximos de controle por cenário são 188,5 / 170,2 / 250,9 / 265,1 / 174,7 ms; os máximos do limite superior evento–painel são 73,7 / 136,3 / 112,8 / 94,5 / 90,7 ms, na mesma ordem. Os limiares de 2 s / 3 s são smoke thresholds; n=30 não estabelece cauda de produção ou SLA. R é limite causal conservador, sem inventar timestamp de commit ou alinhamento de relógios.

Cargas observadas sem lacunas, 136 amostras de recursos, sem OOM ou mudanças de ciclo de vida observados. A série do host/cgroup não prova ausência de transientes ou OOM global do host; picos de cgroup são do tempo de vida do container. Os arquivos brutos conservam CPU, RAM, swap, disco, IDs, imagens e ciclos.

O encerramento após exportação terminou sem erros. Consultas por label exata não encontraram containers/redes da rodada; credencial removida, listener 8443 removido, proxy 443 da produção preservado, Funnel desabilitado e aplicação/broker saudáveis. Relatório final protegido: `~/.local/state/kairos-e03-evidence/mac-32ccec58-final.json`, modo 0600, SHA-256 `3e829ea7239d388ff18a04772cddc2b489f7ba88d16b28a2e27319e198f272a6`. A coleta antes do encerramento e a verificação externa permanecem separadas.

## Contador de exceções e reprodução

A coleta tem `loopback_forward_errors=21`, sem classe ou operação registradas no código antigo. O gate automático antigo cobria medidas/cargas/recursos e não condicionava aprovação ao contador. Não é possível classificar retroativamente cada ocorrência nem declarar o transporte antigo sem erros.

O ensaio diagnóstico Linux/HTTPS `9707878f91724be0bfa73467145b4075`, no mesmo código, registrou 20 exceções, todas `ConnectionRefusedError`, de 03:14:21.564 a 03:14:23.484 UTC. A primeira coleta de recursos foi às 03:14:23.724 UTC, depois de a inicialização autenticada terminar. Não houve nova exceção durante os cinco cenários e 150 negações. Isso reproduz um mecanismo suficiente para explicar o contador: `prepare` consultava a API pela ponte a cada 100 ms antes de o servidor temporário escutar. Não comprova que as 21 ocorrências históricas tenham a mesma classe.

O script externo de diagnóstico terminou com exit 1 por uma asserção residual que esperava uma amostra em vez de 150; não se declara essa invocação integralmente aprovada. As 150 medidas e a série de exceções foram preservadas, e o cleanup terminou completo/sem erros. A asserção será corrigida para a validação seguinte, sem descartar esta tentativa.

## Correção do harness

O launcher passa a esperar o listener privado diretamente antes de usar a ponte. Apenas recusa de conexão é retentada nessa etapa, com prazo e contador próprio; outras falhas propagam. A autenticação pelo endpoint existente continua obrigatória depois da espera. Nenhum listener público, endpoint ou limite foi adicionado.

Durante operação, exceções da ponte têm logging com classe allowlisted e operação fixa, sem mensagem da exceção, endereço do cliente ou bytes da conexão. O resumo agrega contagens por classe/operação. Falha, rejeição ou transporte indisponível impede `gate=passed`, inclusive se aparecer depois da consolidação e antes do relatório final de encerramento. As 150 amostras originais não são editadas.

Regressões iniciais: seis casos RED (incluindo duas aprovações incorretas apesar de falha/rejeição real no contrato do relatório), depois seis GREEN. Integração inicial: 60 testes passaram; fixture de cleanup parcial atualizada para o contrato completo de diagnóstico, sem dispensar checks de daemon. Teste adicional de privacidade e verificações finais serão registrados após execução.

O aceite formal do E0 continua pendente da matriz E0.2, da revisão das evidências e da decisão explícita sobre limites/lacunas. A nova coleta não habilita E1 ou prova suporte ao backend nativo macOS.
