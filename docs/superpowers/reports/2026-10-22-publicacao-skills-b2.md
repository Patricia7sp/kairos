# Publicação coordenada de skills — B2

Implementação validada localmente em 23/10/2026 na branch
`feat/publicacao-skills-b2`. **Revisão independente final e CI remoto
pendentes; sem merge, deploy ou migração de produção.**
Base de código: `44d890ca632b40b78a21b62c5e05622200a5b23c`;
plano aprovado: `02fb85e`.

T1–T6: `ff23200`, `1103f0c`, `fb5e71b`, `abc67f8`, `7ca94d9`, `c767cda`.
[Plano](../plans/2026-10-22-publicacao-skills-b2.md) e
[spec](../specs/2026-10-21-publicacao-skills-b2-design.md).

## Comportamento

Publicação exige fonte confiável, política viva, lock comum e uma conexão.
Aquisição em quarentena emite recibo ligado à raiz e à árvore escaneada.
Staging é cópia, inclusive entre devices; instalação e snapshot anterior
ficam no mesmo filesystem. PREPARED precede movimentos; COMMITTED precede as
confirmações independentes de manifesto e limpeza. Confirmações não reinstalam
a árvore nem ressuscitam adoção. Origem verificada não concede autoria USER
ou autoridade de remoção.

`kairos sync now` usa o coordenador e informa IDs, confirmações e pendências.
Resultado parcial retorna falha; contexto/recibo ausente é recusado antes de
efeitos. Promoção substitui somente com intenção foreground sobre a árvore
exata. Não foram criados comandos de hub/download/restore.

O schema v11 é aditivo e tornou-se padrão somente em T7. Eventos antigos
não são reescritos; leitores legados não migram implicitamente. Clientes
antigos são recusados em v11, inclusive após reparo. Restauração com novos
inodes conserva história e inventários sem renovar instalação, adoção,
binding ou autoridade sobre uma fonte externa.

Backup segura o lock durante a captura autoritativa e atesta inventários
antes/depois. Home v11 novo sem skills é aceito sem criar a pasta. Se v11
surgir durante captura inicialmente legada, a parcial é descartada e repetida
sob lock; um banco previamente observado não pode ser trocado por outro inode.
Fsync do arquivo, replace e fsync do pai precedem sucesso.

## Validação integrada

Comando executado pelo Root, **exit 0**:

```bash
PATH="/tmp/kairos-b2-validation/codex/bin:/home/paty7sp/.local/bin:$PATH" \
SHELLCHECK_OPTS='-e SC1091' PYTHONDONTWRITEBYTECODE=1 scripts/ci.sh
```

| Check | Resultado da rodada definitiva |
|---|---|
| Python com coverage | 4050 passed, 21 skipped, 33 deselected, 6495 subtests; 1145,58 s |
| Imagem real | 32 passed, 26 subtests, nenhum skip; 190,83 s |
| Docker offline | 26 passed, 1 skipped; 59,90 s |
| TypeScript | Três frontends aprovados |
| Vitest | ui-tui 17; web 204; desktop 18 aprovados |
| Ruff check/format, shellcheck, hadolint v2.12.0 main/stub | Aprovados |
| uv lock, recall e builds real/stub/broker/worker | Aprovados; recall 20 testes |
| Sonar scanner/processamento | exit 0 / SUCCESS; Quality Gate ERROR |

Python 3.11.16, Codex 0.154.0, Node 22.22.1 e uv 0.12.5. Fonte congelada
durante toda a execução; fingerprint antes/depois idêntico:
`40d43040b1f9a1c6c1928e61cb152ed924bcc6c67699d076902ac432578f18d9`.
Depois dessa rodada, só a documentação de entrega foi atualizada.

Os 33 deselects unitários são os 32 casos de RealImageTests, executados
separadamente, e runtime_live. Skips unitários são opt-ins de integração e
capacidade de ambiente não ativados nessa fase; o trecho Docker offline
ativa seus testes. Seu único skip exige AppArmor/seccomp publicados no host
(`KAIROS_RUNTIME_SANDBOX_TEST`), ausentes nesta execução. Não foram ativados
runtime live, credenciais de modelo ou chamadas pagas.

Auditorias npm de produção: zero vulnerabilidades nos três frontends.
Manifests e lockfiles de dependências não mudaram.

## Interrupções, clientes e restauração

Matriz real: **102 SIGKILLs**, 50 iniciais e 52 substituições, no host e na
imagem. Cada fronteira exige sinal pelo Pipe, processo vivo e exit -9.
Inclui cada fsync descoberto no caminho de controle real de staging, pais,
manifesto e limpeza, além de renames, PREPARED/COMMITTED e confirmações.
Retomada repetida preserva ID, inodes e história; outro escritor é bloqueado
enquanto houver pendência. Política alterada entre processos, fonte ausente
e raiz byte-idêntica conservam as versões pendentes. Órfãos sem PREPARED
confirmado não são adotados nem apagados.

O aceite monta somente dois scripts RO, importando módulos instalados em
`/opt/kairos`: UID 10000, rede none, caps drop ALL, no-new-privileges e rootfs
RO. Home e quarentena são tmpfs descartáveis com st_dev distintos comprovados.
Inclui manifesto/limpeza pendentes, backup integral, restore completo e banco
isolado; nenhum restore transfere autoridade.

Clientes v8/v10 são wheels reais em venvs isolados. V10 é extraído da base
`44d890c`, com dependências sincronizadas por hashes e proveniência de revisão,
lockfile e isolamento. Wheel v10 reproduzido:
`01dc1ee7550d9b3c158a6a883742c9b2ef0bb7e1359e408d4e7d43f1031a1c19`.
Controles v10 executam seis ações antes do upgrade; depois de upgrade/reparo
as recusam sem mudar SQL, inodes ou bytes. Capabilities 8/9/10 também foram
exercitadas por transações reais em todas as famílias participantes.

Mutações de sensibilidade: recuperação desativada falhou no host e na imagem;
ativação de volta a v10 falhou nas seis ações antigas. Regressões de schema
corrompido falharam antes das correções de erro público/SQL.

## Falhas encontradas e corrigidas

A primeira rodada completa terminou exit 1: quatro falhas unitárias e uma
na imagem; não foi contada como aprovação. Novos testes RED→GREEN cobrem
backup v11 sem skills, upgrade concorrente durante snapshot e criação de DB
após a decisão de ausência. O comando de backup durante bootstrap s6 passou
na imagem reconstruída.

Fixtures de corrupção agora removem referências da timeline ativa e recompõem
guards para não passar apenas por guard ausente. A recuperação usa conexão
real já aberta ao testar dados corrompidos; a recusa mais cedo do bootstrap
v11 continua coberta separadamente. Regressões direcionadas: 102 testes e
336 subtestes; backup: 45 testes e 15 subtestes.

O teste sob 160 descritores manteve o lote de 10 itens e sua repetição. Probe
com coverage concluiu exit 0 em 139,21 s, com dez cópias completas e dez skips;
o deadline passou de 60 para 300 s. A mutação sem liberar observações da fonte
continuou sendo uma falha de recursos; limite e inventários não foram reduzidos.

T5 teve uma tentativa integral com segfault após dump adicional do faulthandler
e outra invalidada por edição de fixture durante execução. Nenhuma foi contada
como verde. Sua rodada estável passou com árvore congelada; a causa nativa
não foi atribuída ao produto sem evidência. A rodada integrada definitiva de
T7 acima passou com todas essas superfícies ativas.

## Artefatos locais

| Imagem | ID observado após o CI |
|---|---|
| kairos:test | sha256:ce3239fa220781a95351f6e599ab0243d33a9968bfb9c533b950cb060e63b97b |
| kairos:stub | sha256:f88451c93e3b634bde9bd59f3a28d07ab7f7b23590c610cd7077a91737aebcc6 |
| kairos:runtime-broker-test | sha256:8fc0fc8a701631c2dcc6de0816549614e4c3f606b4b508883e2d2e3bba101f80 |
| kairos:external-sandbox | sha256:4a8800ed2254fa4ee1d1ec7dff51b28ed259b87a58dcdda84b804d7f7222c46d |

## Limites e gates pendentes

O pacote atual contém SKILL.md acima de 64 KiB: preflight sem pendência
recusa explicitamente, sem aumentar limites nem alterar conteúdo de terceiros.
**Não há aceite de sync completo desse pacote.** Fonte excluída sem
source_cleaned continua COMMITTED com limpeza pendente; ausência não comprova
autoria da exclusão. Quarentena externa não entra implicitamente no backup.

[Sonar local](https://sonarcloud.io/dashboard?id=Patricia7sp_kairos): tarefa
`AaFnSX5ztdTaAiBCqzMf` processada com SUCCESS, **Quality Gate ERROR**,
new_reliability_rating=3 (limite 1). Há achados fora do diff; em arquivo
alterado, o primeiro scan apontou I/O síncrono do dispatcher async do backup.
O consumidor de produto é um comando via asyncio.run; a decisão e seu custo
constam abaixo. Não é aprovação do commit final remoto.

Revisão independente final ainda pendente. CI remoto e seu Quality Gate devem
ser conferidos na revisão efetivamente proposta antes de merge. Nenhum merge,
deploy ou migração de produção foi executado.

## Decisões tomadas durante a execução

- Ruling: a condição de base limpa significa código igual a 44d890c, mantendo o plano aprovado 02fb85e na história — evita perder o documento aprovado e não modifica o produto — custo se errado: rever apenas a base documental antes de T1.

- Ruling: os títulos T1…T7 tornam-se Task 1…Task 7 no plano local — task-start/task-brief exigem esse formato — custo se errado: alteração documental de sete cabeçalhos; contratos e ordem preservados.

- Task 1: Ruling: capability 11 requer schema atestado antes de registro; validação falha registra capability 0 e propaga erro — evita capability válida em estado parcial — recuperação estrutural continua exigindo conexão controlada para repair_publication_objects.

- Ruling T1: has_org_schema reconhece v11 antes de T3 para permitir o pai organizacional no PREPARED; teste legado de schema agora prova migração explícita e idempotência sem obrigar ativação. Manutenção de manifesto foi exercitada RED removendo sua capacidade e depois restaurada GREEN; as primeiras execuções desse caso já eram verdes e não contam como RED.

- Task 2: Ruling: adicionar quarantine_identity=(device,inode,mount) ao recibo v11 ainda não publicado — source_proof só identifica a árvore adquirida e source_key fornecido pela composição não vincula a raiz de quarentena; a spec exige recusar recibo transferido para outra raiz — custo se errado: ajustar o DTO e fixtures v11 antes de publicar; homes v10 não mudam.

- Task 3: Ruling: adaptar também repositories/skill_adoptions.py e repositories/skill_removals.py, além dos arquivos enumerados em T3 — as consultas diretas ainda buscavam eventos na timeline v9/v10 congelada, impedindo adoção e remoção novas em v11; os contratos de T3 exigem os leitores das quatro famílias — custo se errado: duas adaptações localizadas de seleção de timeline, sem mudança de linhas canônicas ou schema. Regresso de remoção v11 RED observado antes da correção; adoção/revogação pós-publicação também exercitadas.

- Task 4: Ruling: integrar também org_policy_guard.py, publication_sources.py e publication_files.py — T4 precisa autorizar somente seu próprio draft pendente, reutilizar o repositório da conexão única, descartar staging provado e observar fds/stamps para commit sem I/O de árvore na transação. A observação imutável da fonte termina só imediatamente antes do deletor verificado após COMMITTED; raiz/contexto/política e árvores instaladas continuam atestados em cada guard — custo se errado: endurecer helpers compartilhados antes de T7, sem mudar journal v11 nem v10.

- Task 4: Ruling: fonte ausente após exclusão sem evento source_cleaned continua COMMITTED com limpeza pendente — o journal não possui cleanup_prepared nem prova de autoria da ausência; a spec exige prova original para limpeza — custo se errado: pendência conservadora exige tratamento explícito futuro, nunca inferir sucesso por nome/ausência. Manifesto com delta permanece pendente até a projeção efetiva de T5; nenhum evento fictício é emitido em T4.

- Task 5: Ruling: separar leitura estrita/compare-and-apply em publication_manifest.py — evita ciclo entre coordenador e sync e mantém o leitor permissivo legado somente para leitura compatível; assinatura pública reconcile_manifest permanece reexportada por sync — custo se errado: consolidar helper, sem alterar contrato ou schema.

- Task 5: Ruling: ativar o subcomando público já declarado `kairos sync now`, também ajustando commands.py — skills sync tinha handler, mas nunca foi exposto pelo parser; teste do executável real revelou isso. Nenhum comando novo de hub/download/restore é criado — custo se errado: rever somente o roteamento e status do subcomando existente.

- Task 5: Ruling: pendências conhecidas são reconciliadas antes do preflight dos demais itens do pacote — CLI real encontrou SKILL.md do pacote acima do limite de 64 KiB; isso não pode esconder ID COMMITTED pendente de manifesto. Sem pendência, o pacote ainda é validado antes de copiar; falha é explícita, sem ampliar limites ou corrigir conteúdo de terceiros — custo se errado: rever a ordem do preflight e tratar o conteúdo em recorte próprio, sem sucesso fictício.

- Task 5: Ruling: metadados temporários de revalidação fecham cada fd imediatamente; observações da fonte bundled são liberadas somente depois de publicação completa, e preflight usa capturas sem manter todas as fontes abertas. As mesmas provas e guards permanecem antes/depois dos efeitos — custo se errado: revisar lifetime de descritores, sem retirar vetos ou revalidações. Teste sob RLIMIT_NOFILE acrescentado; mutação sem liberação confirmou recusa por I/O. Primeira configuração de escala excedeu deadline de 40s e não conta como GREEN.

- Task 6: Ruling: backup v11 observa somente a versão antes do lock; journal, snapshot SQLite e inventários ficam sob o lock comum. Snapshot v11 que apareça no caminho legado é recusado, evitando migração/corrida sem lock; legado não ganha lock nem migração implícita — custo se errado: ajustar detecção/coordenação de backup, sem liberar arquivo parcial.

- Task 6: Ruling: inventário da instalação COMMITTED corrente permite edições locais com a mesma raiz (dev/inode/mount), mas captura bytes/modos atuais antes/depois; staged e previous imutáveis exigem prova canônica inteira. Instalação antiga já substituída/removida não é exigida novamente no destino; seus snapshots conservados continuam obrigatórios — custo se errado: endurecer/refinar referência por estado, nunca renovar autoridade por hash.

- Task 6: Ruling: árvore de publicação ainda corrente pode estar no retired de remoção A2 pendente — DTO aceita esse caminho fixo derivado do ID canônico de REMOVE; somente um candidato com raiz dev/inode/mount correspondente e prova integral é reconhecido, sem escolher pasta por nome ou bytes. RED→GREEN com adoção foreground e remoção real interrompida depois do rename — custo se errado: restringir/refinar essa referência cruzada, mantendo bloqueio e snapshots.

- Task 6: Ruling: banco transplantado com home_id histórico não pode produzir inventário vazio por filtro do home atual — backup integral recusa referências estrangeiras explicitamente, enquanto diagnóstico histórico permanece somente leitura. RED observado: banco isolado em perfil inicializado substituía a saída e ignorava árvores ausentes; novo guard verifica todas as operações, inclusive fora do filtro atual — custo se errado: tornar backup histórico mais flexível sem omitir artefatos nem reatribuir autoridade.

- Task 7: Ruling: fixtures de timeline congelada de adoção/remoção fixam migrate target=10, o mesmo schema que exercitavam antes de ativar v11; testes B2 continuam exercitando projeção de todas as famílias na timeline nova. Corrigir escapes de DatabaseError em escritores comuns e de remoção com register_mutation_writer compartilhado, e converter recusa de prefixo inválido na migração v11 para erro SQL consistente — três regressões específicas observadas RED→GREEN, sem alterar canônicas — custo se errado: refinar fronteiras públicas/fixtures sem retirar a recusa de corrupção.

- Task 7: Ruling: backup v11 sem diretório skills atesta ausência sem criar a área. Se v11 surgir durante captura inicialmente legada/sem banco, descartar inteiramente a parcial e repetir uma única captura sob lock, mantendo home e inode do banco já observado; mudança de banco previamente existente continua recusada. Guard de versão agora roda antes de ler canônicas da cópia preliminar. Substitui a recusa conservadora da transição de T6 para suportar bootstrap concorrente real da imagem — custo se errado: voltar a recusar essa transição, nunca publicar a parcial sem lock. RED→GREEN de home vazio, upgrade10→11 durante snapshot (flock real verificado nas observações), criação de DB após decidir ausência; backup45 passed/15 subtests.

- Task 7: Ruling: limite do teste de FDs continua160 e lote10 com repetição; deadline passa de60 para300s depois de probe real com coverage concluir em139.21s, sem erro e sem reduzir inventário. Fixtures de corrupção removem referências da timeline ativa e recompõem triggers para não passar por mero guard ausente; recuperação de snapshot corrompido usa conexão real já aberta para testar o serviço, separadamente da recusa correta do bootstrap v11. Testes exigidos da regressão102 passed/336 subtests — custo se errado: rever deadline/fixtures sem dispensar gate de recursos ou corrupção.

- Task 7: Ruling: manter I/O síncrono do comando backup apesar do aviso Sonar S7493 — único consumidor de produto é handlers.py via asyncio.run por comando; não há consumidor web/async concorrente. Mover só fdopen não tornaria o tar assíncrono, e offload requer contrato de cancelamento/cleanup compatível com a captura sob lock. Não silenciar regra nem declarar Quality Gate verde — custo se errado: CLI fica ocupada durante backup; novo consumidor concorrente exige implementação de cancelamento e offload completa.
