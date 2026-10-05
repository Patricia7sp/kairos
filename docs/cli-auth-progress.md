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
  preservado. Sem commit, merge ou deploy nesta entrega.

## Verificação

Testes escritos antes da correção reproduziram segredo em texto plano,
logout sem remoção no cofre, rejeição ausente para cofre bloqueado/fonte
externa e provedor desconhecido. Casos adicionais reproduziram falhas
após gravação/exclusão real e exposição de material sensível em erros de
backend. As correções foram verificadas com o cofre criptografado real em
diretórios temporários, gateway real sem rede e keyring de teste.

Suíte direcionada: 129 testes e 319 subtestes passaram. Ruff check/format,
`git diff --check` e help do executável passaram.

Validação final, após os ajustes de revisão (`uv run pytest -q`): 3075
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

Não houve build de imagem nem validação com credenciais reais. Testes de
imagem sem os pré-requisitos foram pulados; o caminho live não foi
habilitado. O gate de merge continua bloqueado pelas duas falhas acima.

## Próximas etapas

Restrição de workspace, autorização para automações sem terminal,
integração de skills no runtime e workflows duráveis continuam pendentes.
Não foram realizadas chamadas a provedores com credenciais reais.
