# Índice automático de skills na CLI — integração e implantação

## Ponto de retomada

Em 25/10/2026, a raiz principal ainda estava em `feat/hooks-de-stream`, enquanto
o desenvolvimento recente estava no worktree `.worktrees/backup_sync`, branch
`docs/indice-automatico-cli`. A base era a main `8902010`, com o PR #117
integrado. A B2 e o backup síncrono já estavam integrados pelos PRs #114/#115.
O fechamento documental #118 continua separado.

O desenho do #119 e sua execução já estavam aprovados. T1 (`d30c801`) e T2
(`5d22d03`) estavam concluídas; T3 tinha testes RED registrados e alterações
de CLI ainda não commitadas. A retomada preservou esses arquivos e finalizou
a etapa em `bcc7cff`, com ajuste das fixtures em `a8e311e`.

A integração revelou também uma corrida anterior no bootstrap do container:
`doctor` e dashboard tentavam migrar o banco virgem ao mesmo tempo. O teste
de imagem falhou com `database is locked`; uma reprodução com SQLite real
confirmou a falta de retry no `doctor`. O commit `bf7fb14` aplica a escada de
retry existente somente à migração inicial, sem tratar corrupção como sucesso.
O teste falhou antes da correção e passou depois. A revisão independente do
ajuste não encontrou problemas.

## Resultado

`run` e `chat` passam a disponibilizar o índice automaticamente, inclusive
no interativo. `--skills-catalog` exige catálogo válido e modelo apto;
`--no-skills-catalog` desliga índice/leitura sem I/O de catálogo. A última
opção prevalece. Somente índice entra no prompt inicial; corpos e referências
continuam sob demanda por `skill_view`.

Ausência benigna e modelo sem ferramentas confirmadas produzem aviso e
permitem o chat. Erros, corrupção, links e instalações só com entradas inválidas
continuam recusados. Snapshot positivo não se atualiza implicitamente; cada
turno tem capacidade e orçamento próprios. Desligar não apaga histórico.
Runtime, Web e gateways preservam seus contratos. Schema, dependências,
manifestos e limites permanecem iguais à base.

## Integração e deploy verificados

Após autorização explícita, o [PR #119](https://github.com/Patricia7sp/kairos/pull/119)
foi integrado por squash em `224532e8283298c65f287cbf6d507b4a32425429`, em
25/10/2026. O pai é `8902010`, e a árvore integrada é idêntica ao head revisado
`5b6be7e`: `65406540824e2eb6740fdd6a0d14c665c4487826`. O diff pós-merge foi
conferido e não removeu arquivos.

| Verificação final | Resultado observado |
| --- | --- |
| Suíte local integral no head revisado | 4.155 testes, 6.542 subtestes, 21 skips e 33 deseleções; exit 0 em 720,03 s |
| CI do PR | [#318](https://github.com/Patricia7sp/kairos/actions/runs/40238269756), nove verificações e Sonar aprovados |
| Sonar do PR | Zero apontamentos abertos ou aceitos, zero hotspots, cobertura nova 97%, duplicação 0% |
| CI da main | [#319](https://github.com/Patricia7sp/kairos/actions/runs/40241432910), completo e verde, incluindo deploy |
| Unitários da main | 4.152 testes e 6.542 subtestes; 24 skips e 33 deseleções, em 631,49 s |
| Imagem na main | 32 testes e 26 subtestes, em 120,39 s |
| Docker offline na main | 26 testes e um skip do perfil de host opcional, em 37,63 s |
| Sonar da main | Quality Gate aprovado, cobertura nova 95,9%, duplicação 0%, zero hotspots |
| Deploy Komodo | Job `121316316003`, Update `6ade885958764b0d43dff8a4`, concluído com sucesso |
| Revisão implantada | Checkout gerenciado em `224532e`; nove módulos de produção alterados com hashes iguais aos revisados |
| Aplicação | Container `running` e `healthy`; smoke exit 0 e consulta independente HTTP 200/`status: ok` |

Os 12 apontamentos introduzidos no recorte do PR foram corrigidos em
`2f5d586`: captura de cada entrada extraída, sete asserções compostas separadas
e quatro preparações movidas para fora dos blocos de exceção. A revisão
independente aprovou os deltas. Limites continuam compartilhados entre entradas;
falhas de limite continuam sendo propagadas. Os 148 testes das famílias de
catálogo passaram após a refatoração.

A suíte integral revelou também uma dependência intermitente do teste de
retenção do lock em `/proc/<pid>/fd`. `5b6be7e` verifica o filho ainda vivo e a
recusa real de `flock` após a morte do host. As 23 verificações da família e a
suíte integral passaram; revisão independente sem achados. O runtime de
produção não mudou nessa correção do teste.

O wrapper do deploy retornou `commit '?'`, pois o campo esperado não veio no
Update. A revisão foi conferida diretamente no checkout gerenciado e pelos
hashes dos nove módulos instalados no container. A implantação foi verificada
por esse estado observado, pelo resultado do Komodo e pelo healthcheck.

A main mantém 1.061 apontamentos no período analisado, a mesma contagem
observada na base `8902010`. Esse backlog tem escopo diferente dos zero
apontamentos do PR. A cobertura nova da main também usa um período próprio;
95,9% na main e 97% no PR pertencem às respectivas análises.

Provas observadas no deploy em
[proof.json](2026-10-25-indice-automatico-cli/proof.json), com
[manifesto SHA-256](2026-10-25-indice-automatico-cli/sha256.json).

## Validação local anterior à primeira publicação (`1fcd43e`)

- Suíte unitária integral: **4.155 testes e 6.542 subtestes aprovados**,
  21 skips e 33 deselections, exit 0, em 953,79 s. Comando equivalente ao
  job unitário, sem cobertura e com diagnóstico de testes longos:

  ```bash
  env -u COVERAGE_PROCESS_START -u COVERAGE_FILE -u KAIROS_COVERAGE_ROOT \
    PATH="/tmp/kairos-codex-0.154.0/bin:$PATH" \
    .venv/bin/python -m pytest -q -x -rs -o faulthandler_timeout=60 \
    --deselect tests/test_container.py::RealImageTests
  ```

  As deselections são a imagem, executada separadamente, e `runtime_live`.
  Opt-ins Docker e aceites entre devices foram cobertos pelas rodadas
  específicas; harness de cobertura e perfil de host AppArmor/seccomp
  permaneceram sem opt-in. Os dumps de diagnóstico em testes longos não
  interromperam a rodada nem representaram falha de teste.

- CLI real com adapter local fictício: 28 testes passaram em 3,43 s.
- Vertical de catálogo, CLI, runtime, posse e retries: 258 testes passaram em
  19,63 s após atualizar a expectativa antiga do default no teste da CLI.
- Ferramentas, automações, seleção explícita e busca: 81 testes passaram em
  10,59 s após completar as fixtures com home e repositório reais. A falha
  anterior era `Home do catálogo não configurado`; produção já fornecia o
  home. O teste de ausência de varredura mantém suas asserções com opt-out.
- Ruff check e format globais: aprovados; 725 arquivos Python conferidos.
  Diff-check, 30 links locais e `uv lock --check` aprovados.
- Revisão independente GPT-6 Luna/high: diff `8902010..bcc7cff` e delta de
  fixtures `bcc7cff..a8e311e`, sem achados Critical/Important/Minor. O revisor
  não executou testes; os comandos e a integração são conferidos pelo Root.
- Correção de contenção do `doctor`: 114 testes e 319 subtestes passaram em
  17,17 s; o teste de migração na imagem reconstruída passou em 11,67 s.
  Revisão independente adicional do delta `a8e311e..bf7fb14` sem achados.
- A rodada global anterior registrou também um timeout de teste de runtime:
  `test_cancel_bounds_interrupt_delivery_and_uses_independent_terminal_evidence[missing_response]`.
  O cancelamento já tinha retornado o desfecho correto; a guarda de 0,3 s
  expirou durante consultas/reenvio SQLite posteriores. `10c569f` restringe
  a guarda à chamada de cancelamento e mantém todas as asserções depois dela.
  O cenário usa agora orçamento de 0,3 s com guarda externa de 3 s, para
  permitir reconciliação com SQLite real; o teste dedicado que observa o
  prazo restante de RPC continua intacto. Os 30 testes do arquivo passaram
  em 15,09 s; revisão independente do ajuste sem achados. Nenhum código de
  runtime mudou.
- Checks de shell, Dockerfiles/hadolint, estrutura dos builds, lock e recall
  aprovados. TypeScript e Vitest dos três frontends passaram: 17 testes TUI,
  204 Web e 18 desktop. Sessões Docker offline: 26 passes e um skip porque
  o opt-in de host AppArmor/seccomp não foi habilitado. Sem benchmark opt-in
  ou chamada a provider pago.

O smoke histórico da imagem de `bf7fb14` passou nos modos auto/on/off, incluindo leitura
sob demanda, ausência de corpos na requisição inicial, avisos em stderr e
NDJSON preservado. Executou sem rede, UID/GID 10000, rootfs somente leitura,
sem capabilities e com limites de recursos; home e banco foram temporários.
Os cinco hashes dos módulos instalados conferiram com os fontes locais.
[Prova histórica da imagem de `bf7fb14`](2026-10-25-indice-automatico-cli-image.json).
Naquela imagem, a família completa passou: 32 testes e 26 subtestes, em
328,90 s. O broker foi reconstruído sobre a mesma base e seu cliente Docker
confirmou versão 29.7.2.

A execução integral de 953,79 s começou com as fontes de produção de `bf7fb14`.
Até a primeira publicação em `1fcd43e`, o único delta executável posterior a
essa execução foi o ajuste de prazo no arquivo de testes do runtime
(`10c569f`); esse arquivo foi reexecutado por inteiro, com 30 passes.
Produção, dependências, lock e configuração relevantes permaneceram iguais
nesse intervalo; o Root conferiu essa relação ao reaproveitar os checks.

A correção posterior do Sonar em `2f5d586` alterou a captura do catálogo.
A imagem local foi reconstruída com essas fontes: 32 testes e 26 subtestes
passaram em 257,61 s, e o smoke auto/on/off passou novamente. Os cinco hashes
instalados conferiram, incluindo `catalog_io.py` após a refatoração. A prova
final está nos campos `local_final_image` e `local_final_image_smoke` de
[proof.json](2026-10-25-indice-automatico-cli/proof.json), separados da
imagem histórica de `bf7fb14`. O delta seguinte foi somente o teste de lock
em `5b6be7e`; a nova suíte integral de 720,03 s executou esse head revisado.
Os resultados dessa execução e do CI pós-merge estão na tabela de fechamento.

A rodada instrumentada foi interrompida para diagnóstico após ficar muito lenta em
validações síncronas de schema do ledger. A inferência inicial de espera
assíncrona estava incorreta; a pilha capturada identificou validação ativa no
teste de recuperação de publicação. Essa rodada não é evidência de aprovação
da suíte nem de cobertura completa. O script retornou falha, incluindo o erro
de doctor posteriormente corrigido; seu exit não foi reinterpretado como verde.
Os checks não afetados foram conferidos e reaproveitados, e os testes afetados
foram repetidos. A cobertura posterior foi gerada nos jobs unitários dos CIs
#318 e #319 e usada pelas respectivas análises Sonar aprovadas, conforme o
fechamento acima. Python 3.11.16 e Codex 0.154.0 foram usados; o pacote oficial
foi verificado pelo SHA-256 pinado no Dockerfile.

## Entrega e limites

A revisão automática de permissões bloqueou as tentativas iniciais de envio,
exigindo autorização para publicar no repositório público. A publicação foi
autorizada e concluída posteriormente; após corrigir os apontamentos e fechar
as validações, a usuária também autorizou o squash-merge e o deploy. Os CIs
#318 e #319, a implantação e o smoke foram concluídos, conforme as provas acima.

O ledger preserva duas decisões anteriores: seguir a aprovação explícita de
execução sem reabrir o plano, e validar os workers no host porque o sandbox
bloqueava o caminho assíncrono. A execução bloqueada no sandbox permanece sem
aprovação atribuída; as autorizações de publicação, merge e deploy foram
obtidas separadamente antes das ações correspondentes.

Referências: [desenho](../specs/2026-10-25-indice-automatico-cli-design.md),
[plano](../plans/2026-10-25-indice-automatico-cli.md),
[guia de uso](../../guia-cli-comandos.md#consultar-o-catálogo-instalado).
