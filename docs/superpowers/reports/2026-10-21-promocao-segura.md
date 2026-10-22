# Promoção de skills sem perda do destino

Estado em 21/10/2026: correção local sobre `f9f9a6d`, main integrada de B1.
Não há merge ou deploy desta correção. B2 está documentada para revisão,
sem implementação nem plano aprovado.

## Defeito e contrato da correção

A revisão pós-merge do [PR #108](https://github.com/Patricia7sp/kairos/pull/108#discussion_r4322011728)
apontou exclusão da instalação antes do movimento da quarentena. Falha no
rename ou mudança de política entre esses efeitos podia apagar o destino
anterior. O fluxo original foi reproduzido pelas três regressões da API
pública, sobre filesystem e SQLite temporários reais.

A correção retira a exclusão prévia em `install_from_quarantine`. O movimento
continua atestado por descritores e usa rename sem substituição. Destino
ocupado recusa, conservando as identidades e os bytes da instalação e da
quarentena. Uma publicação inicial continua instalando de verdade. Não há
`force`, recomendação de apagar o destino para atualizar, journal novo nem
recuperação transacional de publicação nesta entrega.

Rastreabilidade herdada: `_reversa_sdd/skills/requirements.md`, RF-08 e
política de divergência/ADR 013. A restrição deliberada de promoção sobre
destino ocupado está registrada em D-CLI.SKILL.PROMOTE. Os contratos B1 e
os guards de política corrente/proteção registrada permanecem aplicáveis.

## Evidências locais

- Baseline: o hub original de `f9f9a6d` foi carregado em processo isolado,
  mantendo os testes novos e sem alterar a worktree. As três regressões
  falharam pelos motivos esperados: destino substituído indevidamente ou
  ausente após falha de movimento/mudança de política.
- Correção: `tests/test_skills.py tests/test_skill_org_publication.py`
  passou com **54 testes e 11 subtestes**. Ruff check/format dos dois arquivos
  alterados e `git diff --check` passaram.
- Revisão independente por GPT-6 Luna, somente leitura: nenhum bloqueador
  no código. A ambiguidade histórica de B1 sobre manter os contratos pessoais
  foi corrigida com referência à decisão posterior de promoção.
- O exemplo de composição de D-CLI.SKILL.ORG foi executado em home/banco
  temporários: política observada, conexão, migração, serviço, instalação e
  histórico reais, sem pendência. Isso documenta o pré-requisito de #110,
  sem adicionar migração implícita nem mudar o contrato dos leitores.

O CI completo e as integrações Docker foram concluídos nesta retomada,
conforme resultados abaixo. O Codex 0.154.0 foi extraído da imagem local
somente para o PATH das validações. Não houve mudança do executável instalado
nem acesso a home de produção.

Na preparação anterior, o CodeRabbit CLI estava instalado, mas sem autenticação.
A revisão automática de permissões recusou iniciar seu login externo por falta
de autorização específica. Não há resultado de revisão CodeRabbit nesta entrega; a revisão
independente acima foi realizada separadamente por outro agente.

## Revisão retomada em 21/10

Nova revisão independente por GPT-6 Luna, raciocínio alto, conferiu o diff
completo da correção e o desenho B2. Não apontou bloqueador para a preservação
do destino ocupado. Apontou a fronteira pós-movimento: na publicação inicial,
se o rename já ocorreu e o guard posterior recusa, a API retorna erro com a
árvore no destino e a quarentena ausente. Isso também pode ocorrer após falha
de fsync posterior ao rename. Não há perda de uma instalação anterior nesse
caminho; ainda não há journal para reconciliar esse resultado.

Decisão de escopo do Root: manter a correção limitada a retirar a exclusão
destrutiva, sem retirar o guard posterior nem inventar rollback. A garantia de
identidades e bytes de ambas as árvores refere-se à promoção sobre destino
ocupado e à recusa anterior ao movimento. Recuperação da publicação inicial
após efeito pertence a B2; tratá-la como resolvida aqui ocultaria essa lacuna.

Na spec B2, os dois apontamentos importantes foram incorporados: emissor,
registro e verificação do recibo de aquisição; backup/restauração das árvores
privadas e diagnóstico de referências sem artefatos. Os limites das transações
e dos estados duráveis também ficaram explícitos. A revisão técnica não
substitui a aprovação da spec escrita pela usuária.

## Evidências da retomada

- As três regressões foram repetidas com o módulo original de `f9f9a6d`
  carregado em processo isolado: **3 falhas esperadas**, sem alteração de fonte.
- A suíte direcionada atual repetiu **54 testes e 11 subtestes aprovados**.
- O caso de política alterada depois do rename inicial foi reproduzido pela
  API real: conflito explícito, conteúdo no destino e fonte já movida. Esse
  resultado fundamenta a limitação acima, sem ocultar efeito já ocorrido.
- O exemplo de D-CLI.SKILL.ORG foi executado literalmente sobre banco v9
  temporário: migração para v10, instalação, um registro no histórico e nenhuma
  pendência. Nenhuma configuração de produção entrou no processo.
- Imagens stub, broker e worker construídas; broker informa Docker 29.7.2.
  Hadolint fixado em v2.12.0 aprovou Dockerfile e Dockerfile.stub.
- Integrações Docker offline: **26 testes aprovados e 1 skip** por perfil
  AppArmor/seccomp do host indisponível; nenhum provedor real foi invocado.
- O CI integral usou Codex 0.154.0, extraído da imagem local apenas no PATH da
  validação. Três arquivos antigos de cobertura foram copiados e conferidos
  fora do combine atual para evitar mistura com execução anterior.

Logs desta retomada: `/tmp/kairos-promocao-final/`, com `evidence.json`
vinculando código, testes, lockfile, Dockerfile e configuração aos checks.

| Check final | Resultado |
|---|---|
| `scripts/ci.sh`, completo | exit 0, sem etapa pulada; cliente v8 real preparado |
| Python com cobertura | 3931 testes e 6409 subtestes aprovados; 19 skips e 31 deselects |
| Cobertura | somente dados desta execução combinados; XML gerado |
| Ruff check/format, shellcheck, recall e lockfile | aprovados; recall com 20 testes |
| TypeScript e vitest | três frontends aprovados; TUI 17, Web 204, desktop 18 testes |
| Sonar local | análise enviada com sucesso; não atesta Quality Gate do futuro PR |
| Dockerfile | hadolint e build check aprovados; imagem real construída |
| Integração na imagem | 30 testes e 26 subtestes aprovados, sem skips |
| Regressões dentro da imagem instalada | 54 testes unittest aprovados, sem skips |

Imagem validada: `sha256:1fa3c22f61fea8bf2ebd75e00398d47d8f724159e43cb0ea18e8bca1e2da1de2`.
As regressões na imagem usaram UID/GID 10000, rede desativada, root filesystem
somente leitura, capabilities removidas e apenas testes montados como leitura.
Logs `ci.log`, `docker-offline.log`, `image-skills.log`, `baseline-red.log`,
`exemplo-v10.log` e `limite-pos-rename.log` registram os comandos/resultados.
O caminho `runtime_live` permaneceu desabilitado. A nova revisão remota e os
jobs do PR ainda dependem da publicação; não houve merge nem deploy.


## Continuidade

O checkpoint funcional e as specs A/B agora distinguem integração histórica,
correção local e proposta. #109 continua otimização não autorizada neste
recorte; seus números de revisão não foram reproduzidos aqui.

A [spec B2](../specs/2026-10-21-publicacao-skills-b2-design.md) descreve
coordenação por item, manifesto, identidade nova, recuperação, migração,
backup e aceite na imagem real. Sua revisão escrita precede o plano de
implementação. CI remoto e revisão do desenho não são presumidos por este
relatório local.

## Resposta à revisão do PR #111 — 22/10/2026

A revisão fornecida pela usuária identificou que
`test_policy_change_before_move_preserves_both_versions` aceitava conflito por
destino ocupado como prova de recusa por política. O falso positivo foi
reproduzido em dois processos isolados: desativando os guards de
`move_publication` e desativando `OrgPolicySession.revalidate`. Antes do ajuste,
o teste passava nos dois casos. Não houve alteração do código de produto para
essas mutações; os métodos foram substituídos somente em memória no runner.

O teste agora exige também `"atestada"` na mensagem da exceção, distinguindo a
recusa de política do conflito por nome ocupado. Com essa asserção, as duas
mutações falham exatamente por receberem a mensagem de destino ocupado. Com o
código real, os arquivos `test_skills.py` e `test_skill_org_publication.py`
aprovam **54 testes e 11 subtestes**; Ruff check/format e `git diff --check`
também aprovam. As verificações integrais acima pertencem ao estado anterior
a esta asserção; o código de produto, dependências e configuração permanecem
idênticos. A validação remota deve ser conferida no novo head, sem atribuir a
ele o CI de `7fd4a12`.

A spec B2 revisada foi aprovada pela usuária. O plano foi elaborado no
[PR #112](https://github.com/Patricia7sp/kairos/pull/112), ainda para revisão e
sem implementação. A substituição destrutiva em `quarantine_bundle` permanece
fora do PR #111: T2 do plano exige aquisição exclusiva, preservação da entrada
ocupada e teste de colisão com árvore/recibo anteriores.

Pendência condicionada ao merge real de #111: atualizar o status em
[D-CLI.SKILL.PROMOTE](../../decisoes.md),
no item inicial de [functional-progress.md](../../functional-progress.md) e no
topo deste relatório, com commit de integração e evidências do CI/deploy
observado. Até esse evento, a correção continua em PR; aprovação da revisão
não atesta merge nem implantação.
