# E0.2/E0.3 — matriz de plataforma e protocolo de coleta

Aberto em 02/11/2026 às 23:30:35 UTC, base `c6a083f51e12aaddeac8720185ecc55a27d010de`. Contratos: [SPEC v0.2](../specs/2026-11-02-kairos-evolucao-v0.2.md), seções 12–15, e [E0 do plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md).

**Resultado atual: gate do destino pendente.** Mac Intel com 16 GB é o destino informado no plano, não hardware observado nesta sessão. A forma de acesso foi solicitada à usuária; nenhuma conexão remota foi presumida.

## Observações Linux de desenvolvimento

Coleta administrativa de leitura, sem criar worker, abrir cofre, percorrer dados pessoais ou alterar a stack:

| Dimensão | Observação |
| --- | --- |
| Sistema / arquitetura | Linux, kernel `7.0.0-38-generic`, x86_64 |
| CPU / memória observáveis | 4 CPUs lógicas; MemTotal 7.541.440 KiB; SwapTotal 4.194.300 KiB |
| Docker Engine | 29.7.2; servidor Ubuntu 26.04 LTS; mesmo kernel declarado |
| Recursos declarados pelo daemon | 4 CPUs; 7.722.434.560 bytes de memória; cgroup v2 |
| SecurityOptions declaradas | AppArmor; seccomp com perfil builtin; cgroup namespace |
| Artefato de referência | main `c6a083f`; lock SHA256 `1bcfd55b47e5fabb6721832d1134e2dbe92e3d0e7215b899e135962a965c27d8` |

Os valores de memória descrevem o ambiente visível e o daemon. Não provam RAM física do Mac, limites da VM do Docker Desktop nem consumo do Kairos. `docker info` declara capacidades gerais; não substitui `WorkerPolicy.validate` sobre cada worker efetivamente criado.

## Matriz por backend e perfil

| Backend / perfil | Evidência disponível | Situação no E0 |
| --- | --- | --- |
| Linux / aplicação e regressões | CI por SHA, integração de imagem, filesystem e smoke; ver [baseline](2026-11-02-fase2-baseline.md) | Demonstrado no escopo desses testes; não encerra plataforma destino. |
| Linux / worker externo offline | Testes reais de sessões Docker com opt-in e atestação no caminho do worker | Demonstrado para os cenários executados; não equivale a qualquer perfil de scanner ou egress. |
| Linux / sandbox interna opt-in | Código e testes condicionais existentes; opt-in interno não executado nesta retomada | Não aprovado por estes resultados. |
| macOS nativo / filesystem e host | Nenhuma coleta no destino | Indisponível como evidência; não aprovar suporte nativo. |
| Docker Desktop no Mac / backend Linux | Nenhuma versão, configuração ou inspeção real coletada | Pendente. A existência de VM Linux não prova as propriedades exigidas. |
| Perfis futuros de scanner/delegação | Contratos ainda dependem de E1/E4 | Não habilitados. |
| Executor Linux separado | Há ambiente de desenvolvimento observado, mas nenhuma decisão de substituir o destino do plano | Não adotado como alternativa ao Mac. |

## Incompatibilidades e controles a testar

| Fronteira | Dependência ou comportamento atual | Evidência exigida no destino |
| --- | --- | --- |
| Captura/identidade de montagem | [tree.py](../../../kairos_filesystem/tree.py) lê `/proc/self/fdinfo` e usa `os.O_PATH` | Provar a identidade observada por descritor, mudanças de mount, symlink/hardlink/FIFO e substituição durante leitura; ausência da capacidade deve recusar. |
| Criação/restauração | `fstatfs`, `inotify` e filesystem atestado | Rodar casos reais de [TreeIOTests](../../../tests/test_tree_io.py) no backend escolhido; não trocar por `Path.resolve` ou desativar guard. |
| Rename sem sobrescrita | [descriptors.py](../../../kairos_filesystem/descriptors.py) usa `renameat2` | Provar atomicidade/recusa ou apresentar design equivalente revisado antes de adaptar. |
| Identidade do cliente runtime | [host.py `_check_peer`](../../../kairos_runtime/host.py) compara UID via SO_PEERCRED quando disponível; sem a constante retorna sem essa checagem | Registrar a capacidade real, proteções do socket e modelo de ameaça; não chamar a ausência de checagem de equivalência já implementada. |
| Worker Docker | [WorkerPolicy](../../../kairos_runtime/experimental/worker_policy.py) exige AppArmor esperado, imagem imutável, usuário 10000, rede none, nenhum mount, rootfs RO, cap-drop e limites | Inspecionar o container real e testar recusa de divergências. Versão do Docker ou flags solicitadas não bastam. |
| Recursos e encerramento | Worker atual: 512 MiB, 1 CPU, 128 PIDs e stop timeout; execução controla timeout/stdout/stderr | Medir e testar cancelamento/reap, OOM e limites; chamadas internas/tokens/custo não têm enforcement só porque há constantes. |

Os testes mencionados são um catálogo de aceite, não uma execução Mac já realizada. Se algum caminho Linux não tiver equivalente, o design deve manter o perfil unavailable até decisão/revisão, sem fallback para o shell do Mac.

## Coleta inicial no Mac — somente leitura

Executar apenas no Mac autorizado, registrar horário e código de saída de cada comando. Não enviar configurações, ambientes completos, serial/UUID, credenciais, cabeçalhos ou conteúdo do cofre.

```bash
sw_vers -productVersion
uname -m
sysctl -n machdep.cpu.brand_string
sysctl -n hw.memsize
docker version --format '{{json .Client.Version}} {{json .Server.Version}}'
docker info --format '{"os":{{json .OperatingSystem}},"kernel":{{json .KernelVersion}},"architecture":{{json .Architecture}},"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"cgroup_version":{{json .CgroupVersion}},"security_options":{{json .SecurityOptions}}}'
```

Registrar separadamente a versão do aplicativo Docker Desktop e os limites de CPU/RAM/swap/disco configurados. Client/Server.Version acima são versões de Engine/cliente, não substituem a versão do Desktop. Erro/ausência de Docker deve aparecer como indisponibilidade, nunca como resultado vazio aprovado.

Essa coleta não instala nada e não comprova isolamento. A etapa seguinte exige o artefato/lock identificados, fixtures sintéticas e criação/atestação de workers conforme o modelo escolhido. O plano detalhado e o design de qualquer adaptação são fechados após conhecer o destino; não há comando autorizado aqui para desativar AppArmor, tornar o container privilegiado ou montar o home.

## Protocolo E0.3 a fechar antes de implementar o coletor

O [plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md) propõe `ControlSample`, `BenchmarkSummary` e `summarize_samples`; nenhum desses tipos é anunciado como implementado nesta entrega. Não foram criados `scripts/benchmark_phase2.py` nem testes fictícios que apenas passem com valores constantes.

- `controle_local_ms`: relógio monotônico do cliente, do envio autenticado ao ACK emitido após validar e persistir aceitação/negação; não esperar o efeito nem subtrair inferência estimada.
- `evento_painel_ms`: evento persistido até renderização efetiva, com `event_id` e protocolo de alinhamento de relógios documentado. Ausência de medição do browser é lacuna, não zero.
- Cenários: ocioso, conversa/painel, auditoria sintética representativa, execução isolada e recuperação. O desenho do coletor precisa identificar o endpoint/ACK persistido e a projeção realmente servida antes de produzir números.
- Pelo menos 30 controles por cenário durante a auditoria; nearest-rank `ceil(0,95 × n)`, máximo e amostras brutas sanitizadas. Com n=30, p95 é a 29ª observação ordenada.
- Timeout, erro, relógio incompatível e correlação ausente permanecem no resultado. Não calcular percentil só dos sucessos sem declarar a seleção e as falhas.
- Publicar também CPU/RAM/swap/disco do host, VM e serviços, OOM e processos/containers residuais. Uma leitura pontual de `docker stats` não prova ausência de pico ou órfãos.
- Limiares propostos: p95 de controle até 2 s, de evento/painel até 3 s, sem OOM do núcleo nem órfãos. São smoke thresholds; não são SLA ou estimativa de cauda de produção.

Não houve benchmark autenticado/renderizado nesta coleta e não há aprovação desses limiares no destino. Implementar o coletor exige um design verificável sobre os ACKs/eventos existentes; testes de estatística não substituem o cenário real.

## Timebox e saída do gate

Registro de acompanhamento da proposta de cinco dias úteis: início `2026-11-02T23:30:35Z`; referência de prazo `2026-11-09T23:30:35Z`, contando segunda–sexta em UTC, sem calendário adicional de feriados. Isso não cria agendamento ou monitor automático.

Até o prazo, entregar a matriz e as lacunas e obter uma decisão sobre acesso ao Mac, adaptação equivalente ou disponibilização explícita de outro backend. Expirar o prazo não aprova capacidade nem permite escolher infraestrutura silenciosamente.

Saída exigida: SHA/configuração/ferramentas, método/cenário, resultados reais por controle e medições; revisão do [threat model](../specs/2026-11-02-fase2-threat-model.md) e aceite dos limites. Sem isso, E0 permanece pendente e os perfis de I1 dependentes não são habilitados.
