# E0.2/E0.3 — matriz de plataforma e protocolo de coleta

Aberto em 02/11/2026 às 23:30:35 UTC, base `c6a083f51e12aaddeac8720185ecc55a27d010de`. Contratos: [SPEC v0.2](../specs/2026-11-02-kairos-evolucao-v0.2.md), seções 12–15, e [E0 do plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md).

**Resultado atual: gate do destino pendente; topologia confirmada.** Em 04/11 a usuária definiu uso inicial pelo navegador no Mac, com Kairos no servidor Linux existente. E0.2 valida o executor Linux; E0.3 mede também o caminho navegador–servidor. A coleta do Docker Desktop abaixo fica como histórico, sem bloquear esse uso. Não é necessário instalar ou clonar Kairos no Mac. Execução local no Mac seria outra decisão.

## Histórico — coleta manual do Mac em 03/11/2026

Origem: comandos de leitura executados pela usuária no Terminal do Mac, com resultados enviados nesta conversa. Não é execução remota nem atestação independente do hardware. Horário informado por `date -u`: `2026-11-03T01:26:32Z`.

| Campo | Resultado informado | Interpretação |
| --- | --- | --- |
| macOS (`sw_vers -productVersion`) | `26.7.2` | Identificação inicial; compatibilidade do produto ainda não testada. |
| Arquitetura (`uname -m`) | `x86_64` | Compatível com o destino Intel previsto no plano. |
| CPU | `Intel(R) Core(TM) i7-9750H CPU @ 2.60GHz` | Modelo informado; quantidade de CPUs alocadas ao Docker ainda desconhecida. |
| Memória física (`hw.memsize`) | `17179869184` bytes, 16 GiB | Corresponde à capacidade de RAM prevista; não indica orçamento disponível para a VM. |
| Contexto Docker | `desktop-linux` | Nome do contexto selecionado; não prova que a VM/engine esteja funcionando. |
| Docker CLI | `28.1.1` | Versão do cliente; não é versão observada do Engine nem do aplicativo Docker Desktop. |
| Docker Server / info | `Cannot connect to the Docker daemon` no socket do usuário `$HOME/.docker/run/docker.sock` | Daemon inacessível nessa tentativa; não atribuir causa definitiva sem verificar o aplicativo. |

Os campos vazios, `cpus: 0`, `memory_bytes: 0` e `security_options: null` foram impressos junto da falha de conexão. São dados inválidos para a matriz de capacidades, não recursos reais do daemon. Os códigos numéricos de saída de cada comando não foram enviados; há erro explícito nas duas consultas ao servidor.

Pendências desta coleta: versão do aplicativo Docker Desktop, Engine/kernel da VM, CPU/RAM/swap/disco configurados e controles efetivamente observados. O próximo passo é iniciar/verificar o aplicativo Docker Desktop no Mac e repetir as consultas quando o Engine estiver pronto. Essa ação de inicialização é distinta dos comandos de leitura; não envolve instalação ou alteração de política nesta orientação.

### Segunda consulta: daemon acessível

A usuária enviou depois a saída de `docker version` e do `docker info` filtrado, sem novo timestamp nem códigos numéricos de saída. Esta observação posterior resolve a indisponibilidade inicial, preservada acima como histórico; não houve criação ou inspeção de worker.

| Campo | Resultado informado |
| --- | --- |
| Docker Desktop | `4.41.2 (191736)` |
| Cliente / Engine | `28.1.1` / `28.1.1`, API `1.49`; cliente darwin/amd64, servidor linux/amd64 |
| VM Linux | Docker Desktop, kernel `6.10.14-linuxkit`, x86_64 |
| Recursos declarados pelo daemon | 12 CPUs lógicas; `8219107328` bytes de memória, aproximadamente 7,65 GiB; cgroup v2 |
| SecurityOptions | `name=seccomp,profile=unconfined`; `name=cgroupns`; AppArmor não listado |

As 12 CPUs são CPUs lógicas expostas à VM, não contagem de núcleos físicos. A memória declarada pelo daemon não equivale à RAM física de 16 GiB nem comprova o valor configurado no painel. Os limites do painel foram fornecidos posteriormente, no registro abaixo; medições sob carga continuam pendentes.

Para uma eventual execução local no Mac, há lacuna de isolamento: o daemon não declara AppArmor e informa seccomp com perfil `unconfined`. O [contrato atual do worker](../../../kairos_runtime/experimental/worker_policy.py) exige `AppArmorProfile` igual a `docker-default` ou `kairos-worker-runtime`, conforme o modo; no modo sandbox também exige o perfil seccomp explícito. A saída administrativa não atesta nenhum container nem prova ausência de suporte a um perfil seccomp explícito. Worker no Docker Desktop permanece não aprovado, fora do uso inicial confirmado; isso não bloqueia o cliente pelo navegador. Referências: [AppArmor no Docker](https://docs.docker.com/engine/security/apparmor/) e [seccomp no Docker](https://docs.docker.com/engine/security/seccomp/).

### Limites do painel recebidos em 04/11/2026

A usuária transcreveu os valores de Resources/Advanced do Docker Desktop. Data do recebimento nesta sessão; não foi enviado timestamp próprio da consulta. As unidades abaixo preservam os rótulos do aplicativo, sem pressupor conversão decimal/binária.

| Configuração | Valor informado |
| --- | --- |
| CPU limit | 12 |
| Memory limit | 7.9 GB |
| Swap | 1 GB |
| Disk usage limit | 1 TB |

Isso completa a identificação administrativa dos quatro limites solicitados. O limite de disco é um teto para o Engine, incluindo overheads, não espaço livre, consumo atual ou reserva física garantida. Memória do painel, memória exposta pelo daemon e consumo sob carga são observações distintas; não atribuir a diferença a uma causa não medida. Não foi solicitada alteração desses valores. Continuam pendentes atestação real do worker, resolução da lacuna de isolamento e medições E0.3.

## Observações Linux e vínculo com o executor

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

Em 04/11, a consulta ao daemon local `unix:///var/run/docker.sock` confirmou novamente 4 CPUs, `7722434560` bytes, cgroup v2, Ubuntu 26.04, kernel `7.0.0-38-generic`, AppArmor e seccomp builtin declarados. Nesse mesmo daemon, `kairos` (`kairos:local`) e `kairos-runtime-broker` (`kairos:runtime-broker`) estavam healthy. O ensaio usa imagem de teste separada e fixtures sintéticas, sem mudar a stack de produção. Isso vincula o ensaio ao daemon que hospeda os serviços; não prova por si só identidade completa da imagem implantada ou todos os perfis de produção.

Rodada Linux de 04/11, produto idêntico a `c6a083f` no checkout documental `0d40428`: `KAIROS_EXTERNAL_SANDBOX_TEST=1 KAIROS_RUNTIME_SANDBOX_TEST=0 KAIROS_WORKER_SANDBOX=0 python -m pytest -q tests/test_external_sandbox_integration.py tests/test_docker_session_worker.py tests/test_docker_sessions_integration.py tests/test_tree_io.py tests/test_security_file_boundaries.py` → **68 passed, 1 skipped, 27 subtests passed**, exit 0, 50,76 s. O skip é do perfil sandbox interno que exige AppArmor/seccomp provisionados próprios. Resultado delimitado aos testes executados; ainda não é benchmark E0.3 nem atestação integral do deploy.

Após essa rodada: nenhum container com label `io.kairos.external-sandbox=prototype` residual; Kairos e broker running/healthy; `/api/health` respondeu `status: ok`, `ui_present: true`. IDs implantados observados: Kairos `sha256:cea250e02c30c4416f9a56c8f6db8d26fe0c5d80413835b5552e7e6f2917bb65`, broker `sha256:e96a421fbe833e598301650fa0c7d270e4bb016fabc18bb1aa288e76bf7123f4`. IDs não substituem vínculo com SHA/receita de build.

Leitura pontual de `docker stats --no-stream`, sem garantia de ociosidade: Kairos CPU 0,28%, memória 109,2 MiB / limite visível 7,192 GiB, 15 PIDs; broker CPU 100,80%, memória 115,2 MiB / limite 2 GiB, 15 PIDs. São valores observados uma vez, não pico, baseline ocioso ou estimativa de capacidade. O uso de CPU do broker e sua relação com a carga precisam de série/cenário no E0.3; não inferir causa, falha ou aprovação de responsividade dessa amostra.

## Matriz por backend e perfil

| Backend / perfil | Evidência disponível | Situação no E0 |
| --- | --- | --- |
| Linux / aplicação e regressões | CI por SHA, integração de imagem, filesystem e smoke; ver [baseline](2026-11-02-fase2-baseline.md) | Executor escolhido pela usuária; evidência delimitada, sem aceite automático de E0. |
| Linux / worker externo offline | Testes reais de sessões Docker com opt-in e atestação no caminho do worker | Demonstrado para os cenários executados; não equivale a qualquer perfil de scanner ou egress. |
| Linux / sandbox interna opt-in | Código e testes condicionais existentes; opt-in interno não executado nesta retomada | Não aprovado por estes resultados. |
| Mac / navegador cliente | Intel/16 GiB e macOS informados; acesso pelo navegador confirmado, sem checkout | Cliente escolhido; versão do navegador, cenários UI/reconexão e medições do caminho navegador–servidor pendentes. |
| macOS nativo / filesystem e host | Primitivas e runtime não executados | Fora do uso inicial confirmado; suporte nativo não aprovado. |
| Docker Desktop no Mac / backend Linux | Coleta histórica de Desktop/Engine e limites; AppArmor não listado, seccomp unconfined | Fora do uso inicial; worker local não aprovado, sem impedir acesso web. |
| Perfis futuros de scanner/delegação | Contratos ainda dependem de E1/E4 | Não habilitados. |
| Servidor Linux existente | Escolha explicitada pela usuária em 04/11; mesmo daemon hospeda Kairos/broker e os ensaios sintéticos | Destino de execução adotado; nenhuma nova infraestrutura foi escolhida. |

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

## Histórico — protocolo inicial de coleta no Mac, superado para execução local

O bloco abaixo documenta a coleta já solicitada antes de esclarecer a topologia. Não é uma nova etapa do gate atual. O protocolo original limitava a coleta ao Mac autorizado, com horário e código de saída, sem enviar configurações, ambientes completos, serial/UUID, credenciais ou conteúdo do cofre.

```bash
sw_vers -productVersion
uname -m
sysctl -n machdep.cpu.brand_string
sysctl -n hw.memsize
docker version --format '{{json .Client.Version}} {{json .Server.Version}}'
docker info --format '{"os":{{json .OperatingSystem}},"kernel":{{json .KernelVersion}},"architecture":{{json .Architecture}},"cpus":{{.NCPU}},"memory_bytes":{{.MemTotal}},"cgroup_version":{{json .CgroupVersion}},"security_options":{{json .SecurityOptions}}}'
```

Na coleta histórica, versão do Desktop e limites foram registrados separadamente. Client/Server.Version são versões de Engine/cliente. Erro/ausência de Docker não é resultado vazio aprovado para eventual execução local; não constitui bloqueio do cliente pelo navegador.

Essa coleta não comprovou isolamento. A criação/atestação de workers foi direcionada ao servidor Linux confirmado, com artefato/lock identificados e fixtures sintéticas. Não há instrução para preparar workers no Mac, desativar AppArmor, tornar container privilegiado ou montar o home.

Retomada autorizada em 04/11: o [protocolo do worker](2026-11-04-fase2-protocolo-worker.md) fixa artefato, endpoint, preparação e teste sintético existente. O endpoint do Mac respondeu `engine=28.1.1`, `exit_endpoint=0`, como registro histórico. A usuária definiu depois Mac como cliente pelo navegador e servidor Linux como executor; a preparação local no Mac foi retirada do uso inicial. Os critérios de segurança continuam aplicados ao executor real e o gate E0 permanece aberto.

## Protocolo E0.3 a fechar antes de implementar o coletor

O [plano mestre](../plans/2026-11-02-kairos-fase2-plano-mestre.md) propõe `ControlSample`, `BenchmarkSummary` e `summarize_samples`; nenhum desses tipos é anunciado como implementado nesta entrega. Não foram criados `scripts/benchmark_phase2.py` nem testes fictícios que apenas passem com valores constantes.

- `controle_local_ms`: relógio monotônico do cliente, do envio autenticado ao ACK emitido após validar e persistir aceitação/negação; não esperar o efeito nem subtrair inferência estimada.
- Na topologia confirmada, o cliente é o navegador no Mac e o ACK vem do servidor Linux: incluir a latência real de rede. O nome documental não significa loopback. Identificar navegador/versão, transporte e cenário; não inferir renderização no Mac de um teste executado no servidor.
- `evento_painel_ms`: evento persistido até renderização efetiva, com `event_id` e protocolo de alinhamento de relógios documentado. Ausência de medição do browser é lacuna, não zero.
- Cenários: ocioso, conversa/painel, auditoria sintética representativa, execução isolada e recuperação. O desenho do coletor precisa identificar o endpoint/ACK persistido e a projeção realmente servida antes de produzir números.
- Pelo menos 30 controles por cenário durante a auditoria; nearest-rank `ceil(0,95 × n)`, máximo e amostras brutas sanitizadas. Com n=30, p95 é a 29ª observação ordenada.
- Timeout, erro, relógio incompatível e correlação ausente permanecem no resultado. Não calcular percentil só dos sucessos sem declarar a seleção e as falhas.
- Publicar também CPU/RAM/swap/disco do host, VM e serviços, OOM e processos/containers residuais. Uma leitura pontual de `docker stats` não prova ausência de pico ou órfãos.
- Limiares propostos: p95 de controle até 2 s, de evento/painel até 3 s, sem OOM do núcleo nem órfãos. São smoke thresholds; não são SLA ou estimativa de cauda de produção.

Não houve benchmark autenticado/renderizado nesta coleta e não há aprovação desses limiares no destino. Implementar o coletor exige um design verificável sobre os ACKs/eventos existentes; testes de estatística não substituem o cenário real.

## Timebox e saída do gate

Registro de acompanhamento da proposta de cinco dias úteis: início `2026-11-02T23:30:35Z`; referência de prazo `2026-11-09T23:30:35Z`, contando segunda–sexta em UTC, sem calendário adicional de feriados. Isso não cria agendamento ou monitor automático.

Até o prazo, entregar a matriz executor/cliente e as lacunas. A decisão de topologia foi fornecida em 04/11: servidor Linux existente e cliente web no Mac. Não foi autorizada infraestrutura adicional. Expirar o prazo não aprova capacidade; persistem as verificações do executor e medições reais no cliente.

Saída exigida: SHA/configuração/ferramentas, método/cenário, resultados reais por controle e medições; revisão do [threat model](../specs/2026-11-02-fase2-threat-model.md) e aceite dos limites. Sem isso, E0 permanece pendente e os perfis de I1 dependentes não são habilitados.
