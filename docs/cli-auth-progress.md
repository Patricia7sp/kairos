# Autenticação compartilhada entre CLI e Web

## Escopo entregue

`kairos login` e as rotas de credenciais da Web usam
`ProfileCredentialService`, em `kairos_security/credentials/profile.py`.
O gateway continua resolvendo segredos pelo `build_credential_service`.
`auth.json` recebe referências; segredos novos ficam no cofre existente.

`login --provider` oferece prompt oculto; `--api-key` permanece compatível.
O provedor e o método são validados pelo registry compartilhado. O comando
configura uma credencial, sem testar sua validade junto ao serviço externo.

O logout remove todas as credenciais locais do provedor indicado, ou todas
as locais do perfil ativo se não houver provedor. A Web mantém a remoção da
credencial principal. Metadados externos ao pool e credenciais de outros
provedores são preservados em operações direcionadas.

O serviço abre o cofre após adquirir o lock de `auth.json`, serializando
gravação/exclusão entre CLI e Web. Rejeita fontes externas somente leitura e
documentos inválidos. Login sobre entradas legadas exige migração explícita.
Falhas de persistência acionam recuperação de segredos, métodos e texto
original de metadados, incluindo falhas ocorridas após efeitos reais. Não
há garantia de transação atômica entre cofre e metadados após encerramento
abrupto; se a própria recuperação falhar, a operação retorna erro.

## Rastreabilidade e decisões

- Spec: `../hermes-agent/_reversa_sdd/hermes-cli/requirements.md`, RF-05,
  RF-10 e segurança de arquivos de credenciais.
- Decisão existente: `docs/decisoes.md`, D-PC.1, compartilhamento entre CLI
  e Web; cofre e passphrase administrada já existentes no projeto.
- Implementação em `.worktrees/tools-mcp`, branch `feat/cli-tools-mcp`,
  sobre as alterações anteriores de tools/aprovação/MCP. Checkout original
  preservado. A branch foi reaplicada sobre a `main` para manter separado
  o PR #89 de hooks; o PR deste recorte inclui tools/MCP e autenticação.

## Verificação

Testes escritos antes da correção reproduziram segredo em texto plano,
logout sem remoção no cofre, rejeição ausente para cofre bloqueado/fonte
externa e provedor desconhecido. Casos adicionais reproduziram falhas
após gravação/exclusão real e exposição de material sensível em erros de
backend. As correções foram verificadas com o cofre criptografado real em
diretórios temporários, gateway real sem rede e keyring de teste.

Suíte direcionada: 129 testes e 319 subtestes passaram. Ruff check/format,
`git diff --check` e help do executável passaram.

Validação anterior à preparação do PR (`uv run pytest -q`): 3075
testes e 5840 subtestes passaram, 41 foram pulados e 1 deselecionado.
Persistem duas falhas preexistentes de incompatibilidade do Codex:

- `tests/test_codex_supervisor.py::test_real_binary_initialize_start_and_resume_compatibility_probe`
- `tests/test_runtime_host.py::test_pinned_codex_retains_lock_descriptor_after_abrupt_host_death`

Log final: `/tmp/kairos-auth-final-pytest.log`. `scripts/ci.sh --fast`
também executou shellcheck, recall (20 testes), lock e os frontends;
TypeScript passou e TUI/Web/desktop tiveram 17/204/18 testes passando.
Na primeira execução completa do script, `docker build --check` falhou
por timeout consultando o Docker Hub. A repetição direta passou sem
warnings; o teste correspondente passou na suíte final. Log do script:
`/tmp/kairos-auth-ci.log`.

Nessa execução não houve build de imagem nem validação com credenciais
reais. Testes de imagem sem os pré-requisitos foram pulados; o caminho
live não foi habilitado.

## Preparação do PR

A revisão independente encontrou quatro lacunas, reproduzidas por testes
antes das correções: bash não zero marcado como sucesso; primeiro login
com escrita parcial no keyring antes do índice; segredo legado em outro
provedor regravado em texto plano; migração que não reconhecia o campo
`key` gravado pela CLI antiga. As correções normalizam `success=False`,
recuperam o backend mesmo antes da atualização do índice, validam campos
secretos em todo o pool e migram `key` para `api_key`, incluindo entradas
mistas com referência e segredo. A conferência encontrou ainda colisões de
IDs resolvidos; a migração agora verifica unicidade de referências e valida
todos os payloads antes da primeira gravação. As regressões verificam que
segredo, método e documento anteriores são preservados na recusa.

Os 200 testes direcionados passaram. A revisão independente encerrou os
cinco apontamentos e aprovou o código, condicionado aos gates de CI. O
Codex 0.154.0 foi copiado do pacote
do container para `/tmp/kairos-codex-pin.4PdtTl/package` e disponibilizado
somente no PATH das validações. Os dois testes de compatibilidade passaram
com essa versão, sem mudança do pin do projeto. O CI local completo está
registrado em `/tmp/kairos-pr-local-ci.log`; sessões offline, em
`/tmp/kairos-pr-offline-tests.log`. A rodada anterior à última correção
passou integralmente: 3069 testes gerais, 25 de imagem e 26 de sessões
offline. Um teste de sandbox do host foi pulado por depender de perfil
AppArmor/seccomp instalado. A imagem real, o stub e o broker foram
construídos; o broker informou Docker 29.7.2 e os novos módulos foram
importados da imagem real. A validação completa é repetida sobre a árvore
final antes do merge.

## Integração do recorte

O recorte está no [PR #90](https://github.com/Patricia7sp/kairos/pull/90),
contra `main`; o PR #89 permanece separado. A revisão independente aprovou
as correções dos cinco apontamentos.

A rodada local final de `scripts/ci.sh`, com Codex 0.154.0, passou por
completo: 3073 testes gerais, 5840 subtestes, lint, shellcheck, lock,
recall, TypeScript e testes dos três frontends; imagem real construída e
25 testes de imagem com 25 subtestes passando. Nos testes gerais, 16
foram pulados e 26 deselecionados (imagem executada separadamente e live
não habilitado). As sessões Docker offline tiveram 26 testes passando e
um skip por exigir perfil AppArmor/seccomp do host. O pacote de CLI e
autenticação foi importado da imagem real. Merge condicionado aos nove
jobs de verificação remotos verdes; o deploy é pós-merge, conforme o guia
do projeto.

## Próximas etapas

Restrição de workspace e autorização para automações sem terminal foram
integradas e implantadas pelos PRs #92 e #93. A seleção explícita de skills em
turnos únicos do Chat na CLI foi integrada e implantada pelo PR #94: especificação
`superpowers/specs/2026-10-07-cli-skills-design.md` e plano
`superpowers/plans/2026-10-08-cli-skills.md`. Consumo explícito preserva o contexto
no histórico. O PR #95 corrigiu a descoberta de skills bundled em categorias,
preservando autoria, manifesto e opt-out.

O recorte atual implementa catálogo instalado opt-in por turno, snapshot
SQLite imutável por sessão e leitura paginada de SKILL/referências com hashes
verificados. A CLI real foi testada com adapter local fictício, migração,
reabertura, backup/rollback e contenção. Revisão final, gates completos,
integração e deploy deste recorte ainda estão em andamento. Índice automático
por padrão, autoria, curador e workflows duráveis continuam pendentes.
Não foram realizadas chamadas a provedores com credenciais reais.
