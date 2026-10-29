# Fechamento de learn na CLI

O [PR #121](https://github.com/Patricia7sp/kairos/pull/121) foi integrado por
squash em `f474368b2b34bd630388fb1d29d550dad9b0dfd4`, em 27/10/2026.
O pai é `f106ba8`; a árvore integrada é idêntica ao HEAD validado
`babc86e67b9a109ee08377b1462e55bffd4c562e`. O diff pós-merge contém os
24 arquivos previstos, com 2.836 adições e 59 remoções conferidas; nenhuma
correção da base foi sobrescrita.

## Comportamento entregue

No Chat por modelo da CLI, `/learn <pedido>` ativa somente `skill_create`.
O modelo usa conversa ativa e pedido, sem catálogo, `skill_view`, arquivos
ou URLs. A proposta imutável vincula conversa, chamada, nome, conteúdo e hash.
A CLI mostra o texto integral com controles escapados; somente `criar NOME`
autoriza a publicação USER pelo serviço de autoria, com journal e rollback.
Uma criação concluída revoga a capacidade. O prompt de autoria fica no
request transitório e não contamina o histórico persistido nem o turno seguinte.

Revogação antes ou durante a confirmação produz erro público, exit 1 e
orientação para repetir `/learn`. Recusa humana continua retornando 77.
Conflitos anteriores ao journal não recebem ID quando a ausência de registro
é comprovada. Falha de leitura do ledger mantém o ID candidato e o estado
incerto; falha após publicação não se transforma em alegação de ausência.
Cancelamento fecha o stream e drena o escritor, preservando CancelledError
e sua causa se o cleanup também falhar.

## Validação da entrega

| Evidência | Resultado observado |
| --- | --- |
| CI local completo | `scripts/ci.sh`, exit 0; 4.208 testes e 6.551 subtestes, 21 skips e 34 deselects previstos |
| CI do PR | [#328](https://github.com/Patricia7sp/kairos/actions/runs/40546241050), todos os jobs obrigatórios e Sonar aprovados |
| CI pós-merge | [Main](https://github.com/Patricia7sp/kairos/actions/runs/40573120438), completo e verde, incluindo deploy |
| Unitários da main | 4.205 testes e 6.551 subtestes; 24 skips e 34 deselects previstos, em 799,69 s |
| Imagem da main | 33 testes e 26 subtestes aprovados, em 149,60 s |
| Sessões Docker offline da main | 26 testes aprovados; um skip pelo perfil opcional do host, em 36,93 s |
| Revisão independente | Sem achados pendentes; correções observadas em RED→GREEN |
| Sonar do PR | HEAD `babc86e`, Quality Gate OK, zero issues abertas, cobertura nova 95%, duplicação nova 0% |
| Sonar da main | Análise `8bc23911-7489-4eea-93f5-7eb3406632dc`, revisão `f474368`, Quality Gate OK, cobertura nova 95,8%, duplicação nova 0% |
| Deploy Komodo | Job `122439500810`, Update `6ae10474ce4ff60e6b0adaf5`, concluído com sucesso em 27/10 |
| Fontes instaladas em 28/10 | 337 módulos Python rastreados coincidentes por SHA-256 em cada container de aplicação e broker |
| Saúde observada | Aplicação e broker healthy; smoke direto exit 0 e `status: ok` |

O CI local começou em `019bd09`. Até o HEAD final, somente dois testes
passaram a calcular a fixture antes de assertRaises, preservando os mesmos
dados e a operação testada. Ambos foram reexecutados no HEAD final e a
equivalência foi revisada. Código de produção, dependências, configurações e
lockfile não mudaram entre essas duas revisões. Os 84 testes relacionados
e 17 subtestes passaram; a perda real do lease também foi exercitada no
entrypoint da CLI, sem criação e com mensagem pública.

As validações locais usaram o Codex 0.154.0 e o SHA do pacote fixados no
Dockerfile, apenas no PATH dos testes. O Sonar local foi pulado explicitamente;
as aprovações acima são análises remotas com revisão conferida no serviço.
Os resultados da suíte são históricos desta entrega, não uma nova execução
atribuída à atualização documental.

O wrapper do deploy imprimiu `commit '?'`. A revisão instalada foi conferida
por comparação dos módulos Python com a main e pela saúde observada dos dois
containers, sem acesso ao banco ou aos segredos. A prova cobre esses módulos,
não todos os arquivos de imagem nem configurações de produção. Os registros
estão em [proof.json](2026-10-28-learn-cli/proof.json) e no
[manifesto SHA-256](2026-10-28-learn-cli/sha256.txt). O manifesto usa o formato
padrão de `sha256sum`, com caminhos relativos à raiz da revisão histórica;
seus valores são hashes dos arquivos, não credenciais de autenticação.

## Continuidade escolhida em 28 de outubro

O PR #118 (`83e8c92`) já consolidou o fechamento do ledger e do índice; o
PR #120 (`f106ba8`) validou os padrões reais dos limites de execução.
A sequência agora é segurança e confiabilidade do Sonar, seguida por
curadoria de skills. Workflows duráveis e hub permanecem posteriores.

O [inventário da análise atual](2026-10-28-sonar-riscos.json) contém 1.060
apontamentos abertos: 987 de manutenção, 45 classificados como vulnerabilidades
e 28 como bugs. São 73 alertas de segurança/confiabilidade em 21 regras,
a confrontar com o código e testes. Não representam 73 defeitos confirmados.
O Quality Gate aprovado e os zero apontamentos do PR não eliminam esse
backlog da main, que tem escopo diferente.

O MVP não inclui diretórios/URLs, edição por modelo, telemetria, curadoria,
workflows duráveis ou hub. Não houve aceite com provedores reais ou política
organizacional em produção, nem atestação contra outro processo do mesmo UID.
Ollama continua adiado.
