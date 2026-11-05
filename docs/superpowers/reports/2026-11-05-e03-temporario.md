# E0.3 — operação do coletor temporário

Estado: implementação isolada em validação. Não há medição no Mac nem aceite do E0. A produção não recebe flag, gateway ou dados sintéticos deste coletor. Origem: [desenho aprovado](2026-11-02-fase2-plataforma.md#camada-c--seeder-temporário-aprovado-em-0511) e [plano](../plans/2026-11-05-e03-temporario.md).

O Mac usa somente o navegador. Não é necessário clonar Kairos, instalar ferramentas ou iniciar Docker nele. O operador prepara a temporária no executor Linux; a usuária abre a página, informa a credencial própria, mantém a aba visível, inicia a coleta e baixa o JSON.

## Contrato e interpretação

A página usa o Chat normal, a aprovação real e a mensagem `tool_result` persistida. Só envia `deny`. `controle_local_ms` inclui rede e a consulta adicional que confirma a persistência: `max(t_ack_http,t_persist_confirmado_cliente)-t_send_deny`. O HTTP 200 isolado não encerra a medida. Cada amostra retém sessão, `approval_id`, `tool_call_id` e ID do registro canônico; o launcher confere esse registro novamente no servidor.

R conserva stream→DOM e o intervalo causal persistência→DOM `[t_dom-t_stream,t_dom-t_send_deny]`. O limite superior é conservador, sem sincronização presumida de relógios, subtração de rede ou alegação de timestamp exato do commit. Há MutationObserver, elemento visível correlacionado e duas oportunidades de frame; isso não prova emissão física de pixels. O ramo allow executa antes de persistir o resultado e não é representado por estes números.

O JSON preserva denied/timeout/erro e todas as tentativas. São exigidos 30 denies limpos em cada um dos cinco cenários, com p95 nearest-rank e máximo. Falha, duplicata, ausência de prova, ordem incompatível ou aba oculta impedem percentil agregado válido; não há imputação de zero/infinito nem seleção silenciosa de sucessos. Os limiares 2 s/3 s são smoke thresholds, não SLA. `gate_scope=measurements_only`; passar a medição não concede aceite formal do E0.

## Preparação no Linux

Usar checkout limpo e commitado, Python 3.11 e dependências do lock. A imagem de aplicação e a de worker são IDs imutáveis já atestados; tags mutáveis são recusadas. Exemplo de execução no host, a partir da raiz do checkout do coletor:

```bash
umask 077
E03_PARENT=$(mktemp -d)
E03_IMAGE=$(docker inspect kairos --format '{{.Image}}')
E03_WORKER=$(docker inspect kairos-runtime-worker-image --format '{{.Image}}')
uv run --no-sync python -m scripts.benchmark_phase2 \
  --temporary --image "$E03_IMAGE" --worker-image "$E03_WORKER" \
  --directory "$E03_PARENT/run" --ttl 3300 --publish-tailnet
```

A saída contém URL, run_id, prazo e caminho do arquivo protegido da credencial; nunca o segredo. Entregar essa credencial por acesso protegido ao arquivo, sem copiá-la para PR, logs ou conversa. Ela não é a credencial da produção. Usar janela privada no navegador; a URL HTTPS da temporária usa hostname diferente do IP do Kairos atual. Não reutilizar este perfil para dados reais.

Sem `--publish-tailnet`, a ponte fica apenas em loopback para ensaio local. O daemon deste host não publica portas de redes Docker internas; a ponte encaminha um destino privado fixo e tem no máximo 32 conexões. Serve usa exclusivamente HTTPS 8443 no tailnet, verifica porta livre e identidade antes de remover sua regra. A configuração existente em 443 é preservada; não executar Serve reset ou Funnel. Esse HTTPS temporário não transforma o acesso HTTP atual da produção em HTTPS.

O servidor QA roda UID 10000, rootfs somente leitura, cap-drop ALL, no-new-privileges, memória/swap total de 512 MiB, CPU 1, PIDs 128 e tmpfs limitados. Não recebe socket Docker, credenciais de provider ou volumes de produção. O `VOLUME /opt/data` herdado da imagem é sobreposto com tmpfs de 8 MiB; outros VOLUMEs implícitos são recusados. Código/UI são cópias somente leitura, dados e credencial pertencem ao perfil com TTL máximo de 55 minutos. O único seam de comportamento no servidor é o factory do gateway.

## Cargas e recursos

- Ocioso: sem carga adicional; não significa que o servidor inteiro está ocioso.
- Conversa/painel: segunda conexão real do navegador com turnos sintéticos sem ferramentas. Marcadores persistidos de início/fim comprovam a janela concorrente.
- Auditoria: batches de pelo menos 20 s de `run_full_audit` real sobre 128 arquivos sintéticos. O batch sustentado evita amostras nas pausas de scanners de poucas dezenas de milissegundos.
- Execução isolada: teste existente de Codex real, relay offline, ferramenta local e checkpoint, sem alteração no projeto do host.
- Recuperação: teste existente de checkpoint, fechamento/reabertura do broker e retomada do mesmo thread. É um ciclo real de restauração; não é ensaio de SIGKILL nem latência de um método isolado.

Somente a entrada de imagem do worker é substituída pelo ID imutável. Uma subclasse temporária acrescenta label de propriedade mantendo `WorkerPolicy.validate` e verificações reais do kernel. O servidor QA não controla essas cargas; o launcher confiável no host as executa. O driver de auditoria é processo do host, sem cgroup próprio; suas entradas/batches são fixos e o ciclo é limitado pelo prazo do ensaio. Esse limite não deve ser apresentado como quota de memória de container.

Cada receipt deve cair no intervalo de um ciclo de carga concluído com sucesso. Lacuna permanece explícita e torna o ensaio inconclusivo. Para evitar que um worker seja removido entre inspect e leitura de cgroup, a instrumentação do host serializa criação/remoção dos próprios workers com a coleta, por flock privado com timeout. Nenhum lock é instalado na produção ou no servidor QA. Isso acrescenta overhead ao ensaio; a amostragem tem alvo de 2 s, pode esperar durante o ciclo de vida, e conserva os horários reais.

São exportados contadores CPU/memória/swap de cgroup, eventos OOM, reinícios, memória/swap/CPU/disco do host e resíduos. Picos de cgroup são do tempo de vida do container, explicitamente rotulados; a série não prova ausência de transientes nem OOM global do host. VM não se aplica ao executor Linux nativo. Uma observação faltante não vira zero ou aprovação.

## Encerramento e evidências

Após coleta, manter a temporária disponível para download até sair ou vencer o TTL. SIGINT/SIGTERM iniciam encerramento; cargas terminam o ciclo corrente com prazo, e o timeout encerra somente o grupo de processos próprio. Remover instância, rede, listener, código/página, home tmpfs e credencial somente com identidade conferida. Workers residuais só podem ser removidos quando label, ID, nome e imagem imutável corresponderem ao ensaio; nunca usar prune.

O JSON baixado antes do encerramento declara teardown pendente. O operador deve conferir o relatório protegido final no host e a ausência dos recursos próprios depois do cleanup. Sem mensagem de cliente, a execução termina indisponível (69), não com benchmark bem-sucedido. Erro de carga ou teardown é falha explícita. Erros de HTTP/stream não autorizam retry seletivo de uma amostra; repetir exige novo run_id, preservando a rodada anterior.

Nos ensaios preliminares, o VOLUME implícito criou volumes anônimos. Dois puderam ser ligados aos containers próprios por eventos do daemon e foram removidos após confirmar ausência de referências. O histórico do daemon guarda apenas os últimos 256 eventos; volumes anteriores sem vínculo suficiente foram preservados. Não há alegação de limpeza integral de volumes nesses ensaios antigos. O perfil corrigido recusa volumes e usa tmpfs.

A prova de integração local com Chrome Linux é separada da coleta de destino: ela exercita HTTP/WS/SQLite/DOM e cargas reais, mas não mede navegador ou rede do Mac. A publicação da temporária e os resultados finais serão registrados após revisão/checks. O gate E0 ainda exige a coleta real do cliente, avaliação do transporte efetivo e aceite dos limites.
