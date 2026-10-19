# B1 — política organizacional nos serviços de skills

Estado em 2026-10-19: implementação integrada na worktree `removal_design`;
checks locais obrigatórios e regressões complementares concluídos; aceite
remoto pendente na revisão commitada. Não há autorização de merge/deploy B1.

## Escopo e rastreabilidade

Spec aprovada: [design B1](../specs/2026-10-14-politica-organizacional-b1-design.md).
Plano revisado: [plano B1](../plans/2026-10-14-politica-organizacional-b1.md).
Decisão: D-CLI.SKILL.ORG, em `docs/decisoes.md`. Referências herdadas:
`_reversa_sdd/skills/requirements.md:54`, `skills/design.md:93`, §Fluxos
alternativos e `permissions.md` §3.4. O formato local e a continuidade são
decisões novas do Kairos; as specs Reversa não foram modificadas.

A entrega combina observação por descritores (commit `7d1bd5b`), journal de
proteção (commit `7d6836b`) e integração de serviços, recuperação, diagnóstico,
sync e promoção. A migração padrão v10 é ativada junto à sessão obrigatória.
A tarefa 2 isolada não constitui release. Base `origin/main` integrada pelo
merge `b488e54`, incluindo Sonar consultivo e correção anterior de backup.

## Comportamento entregue

- Configuração selecionada uma vez antes do banco mutante; fonte regular limitada,
  parser estrito e erros sanitizados. Sessão revalida identidade, montagem,
  cadeia e conteúdo; home inicialmente ausente passa a ter seus componentes
  observados assim que surgem, até o encerramento da sessão.
- Namespace, binding atual e proteção durável vetam operações destrutivas.
  CREATE/RESTORE preparam recibo e concluem eventos junto à instalação; remoção
  de binding, inode novo e revogação USER não eliminam proteção registrada.
- Recuperação recebe sessão explícita e conserva versões/pendência quando a
  política diverge. Binding novo não autoriza retroativamente publicação antiga;
  recibo preparado mantém bindings ausentes na política corrente.
- Identify conserva o home e informa cobertura e limite de observação não
  persistida, sem caminhos ou rótulos privados. Leitores não migram.
- Sync/promoção recusam alvos protegidos, inclusive ausentes. Promoção também
  recusa journal pendente antes de retirar destino. Sync retorna `blocked`,
  `failed` e progresso pessoal real, com saída CLI 77; compensação vetada conserva
  resíduos mesmo após coleta de lixo.
- Backup/reparo incluem a família v10. Clientes sem capability, v8 e v9 não
  escrevem em nenhuma das famílias participantes de v10, inclusive após reparo.

## Revisão independente e correções

Revisão de 2026-10-19, GPT-6.1 Sol com raciocínio alto, somente leitura,
recuperada da sessão `01a154b5-cf81-7083-b740-b1a0869dc65e`. O revisor verificou
spec versus implementação, continuidade, descritores e publicações. Não houve
achados Critical ou Minor; dois Important impediram aprovação inicial:

1. Home criado durante a sessão não conservava os descritores observados.
   Teste real de serviço reproduziu a troca de home e ancestral nos modos
   `none`/`file` (quatro subcasos RED). Correção incorpora descritores e mount IDs
   à observação duradoura; chamada posterior recusa `conflict` antes de journal
   ou publicação no home substituto.
2. Promoção podia substituir árvore CREATE/RESTORE publicada com conclusão
   pendente. As duas regressões foram RED; guard agora consulta pendências sob
   lock comum, antes de exclusão/movimento. Mantém inode/bytes de destino,
   quarentena, histórico e operação pendente, identificando o conflito.

Passe de correção do Root: 3 testes e 4 subtestes GREEN; suíte B1 completa com
62 testes e 90 subtestes GREEN. O aceite final depende também da suíte integral;
o parecer inicial não foi apresentado como aprovação sem correções.

## Validação e evidências

Logs locais em `.superpowers/sdd/2026-10-14-politica-organizacional-b1/`.
As execuções antigas truncadas não comprovam sucesso do CI integral.

| Evidência | Resultado confirmado |
|---|---|
| Tarefa 2, `task2-focused.log` | 77 testes e 85 subtestes aprovados |
| T3 inicial, `task3-initial.log` | 28 testes e 48 subtestes aprovados |
| SIGKILL nas fronteiras, `task3-kill.log` | 4 testes e 3 subtestes aprovados |
| B1 após correções, `restrictive-umask-final.log` | 62 testes e 90 subtestes aprovados sob umask 0077 |
| CLI identify após correção textual | 19 testes e 26 subtestes aprovados |
| Ruff global check/format e `git diff --check` | aprovados nesta retomada |
| CI inicial, `ci-final.log` | falhou: texto CLI, chamadas indiretas sem sessão, Codex do host incompatível e modos locais de fonte na imagem |
| Suíte corrigida, `ci-verified.log` | 3928 testes e 6409 subtestes aprovados; 19 skips, 31 deselecionados; cobertura combinada e XML gerado |
| Continuação dos passos do CI, `ci-tail.log` | Ruff check/format, shellcheck, recall (20 testes), lockfile, três frontends, hadolint, build check, imagem real e integração aprovados; exit 0 |
| Frontends nessa continuação | TypeScript aprovado nos três; TUI 17, web 204, desktop 18 testes aprovados |
| Integração na imagem real nessa continuação | 30 testes e 26 subtestes aprovados, sem skips |
| Complemento Docker, `docker-supplement-final.log` | 26 testes aprovados; 1 skip explícito de perfil AppArmor/seccomp não ativado no host |
| Serviços e regressões A1/A2, `task3-green-unrestricted.log` | 260 testes e 317 subtestes aprovados, sem skips |
| Publicação, `task4-green-resume.log` | 108 testes e 30 subtestes aprovados |
| B1 sob umask 0077, `umask-resume.log` | 62 testes e 90 subtestes aprovados |

A execução corrigida anterior terminou durante o sensor Python do Sonar; não
concluiu o script integral nem comprova análise remota. A retomada comparou
os SHA-256 dos 781 arquivos de `source-manifest.json`: nenhuma alteração de
código, testes, dependências, configuração de CI ou Docker. A `main` remota
continua em `96fb3c9`. A suíte, o XML e o complemento Docker foram reaproveitados;
os comandos restantes de `scripts/ci.sh`, a partir do lint, rodaram em
`ci-tail.sh`, cópia temporária desses passos sem alterar o script versionado.
O Sonar consultivo será conferido no PR da revisão commitada; o scan local
interrompido não foi apresentado como aprovado. Os testes reais de imagem
complementam a deseleção de `RealImageTests` na suíte unitária; runtime live
permanece desabilitado.

A tentativa complementar de serviços dentro do sandbox ficou sem avançar no
catálogo CLI e foi encerrada explicitamente, sem aprovação presumida. A rodada
fora do sandbox, com timeout de 600 s, concluiu em 134,87 s sem falhas.

As tarefas 3 e 4 ficam no mesmo commit de integração: serviços e escritores
públicos passam a exigir a mesma sessão explicitamente, junto das adaptações
dos chamadores. Custo dessa decisão: eventual reversão desse commit retira as
duas integrações em conjunto. Nenhum comportamento de B2 foi antecipado.

Imagem local validada: `kairos:test`, ID
`sha256:fcb835648b1a8cd2b3e7585cd1def3ec12230ea54efe2fb1e1c013364fa324c4`.

Ambiente: Python 3.11.16, uv 0.12.5, Ruff 0.16.4, Node 22.22.1, npm 9.2.0,
Docker 29.7.2. Runtime fixa Codex 0.154.0, SHA-256 do pacote validado pelo
Dockerfile; host passou a selecionar 0.163.0. A validação complementar usa o
binário 0.154.0 instalado, por PATH restrito ao processo de teste, sem atualizar
a versão suportada nem alterar o comando global.

SHA-256 de `uv.lock`:
`1bcfd55b47e5fabb6721832d1134e2dbe92e3d0e7215b899e135962a965c27d8`.
SHA-256 de `pyproject.toml`:
`55d68d1d4f61a104b35761031c828d026e0ee5dfe1214bace8ab83eaf6ea994b`.
Nenhuma dependência nova de B1; alterações geradas preexistentes em egg-info
ficam preservadas fora dos commits de implementação. A primeira imagem não pôde
importar fontes locais com modo 0600; leitura 0644 foi restaurada nesses fontes
Python de distribuição, sem alterar segredos/configuração. A repetição inclui
build real e imports sob UID 10000; o resultado anterior não foi aceito.

## Decisões e limites

- O recibo preparado inclui `bindings_digest`: distingue conjunto vazio válido
  de perda de binding preparado. Custo se incorreto: revisar formato v10 antes
  de liberar a entrega; não houve migração de produção por esta branch.
- A observação e o veto são cooperativos, sem autenticação de organização ou
  resistência ao mesmo UID com escrita em código/banco/configuração. Custo:
  esse usuário ainda consegue adulterar a proteção.
- Publicação pessoal sem journal transacional geral continua em B2. Custo:
  interrupção abrupta pode exigir resolução manual; não há atomicidade de lote.
- `remove_file` tem contrato testado no guard, sem nova operação pública.
  `edit`, `patch` e `write_file` in-place ficam fora do veto novo.
- B2, otimização do ledger, isolamento e instalação de política em produção
  permanecem fora desta entrega. Runtime live depende de credenciais reais;
  ausência não é substituída por simulação de aceite.

Próximos gates: registrar commit, abrir PR em rascunho e conferir jobs remotos da revisão exata. Merge/deploy
exigem autorização aplicável a B1; autorizações anteriores de A2 não se estendem.
