# Implantação do runtime Docker em produção

Autorização: usuário solicitou a implantação após o aceite dos PRs 13/14.
Base: main 6315a4f. Implementação em worktree isolada; produção ainda intacta.

## Desenho de implantação

A aplicação e o broker precisam compartilhar `state.db`, identidade UID10000 e
socket Unix. O piloto usa outro banco e UID1000; apontar apenas para seu socket
não atenderia ao contrato atual de sessões da Web.

Adicionar um perfil Compose opt-in `agent-runtime` com broker dedicado, imagem
derivada da mesma aplicação e Docker CLI oficial fixado. Só o broker recebe o
socket Docker. A aplicação recebe `KAIROS_RUNTIME_EXTERNAL=1`, desativando seu
host s6 interno; ambos acessam o volume de dados existente como UID10000.
Workers continuam sem rede, mounts ou credenciais. Não modificar a regra
SO_PEERCRED nem transportar credenciais pelo protocolo da aplicação.

Ativação inicial limitada ao projeto sintético já validado, montado somente
leitura no broker e usado por cópia dentro dos workers. O login dedicado do piloto
será preservado na instalação de produção, sem importar o perfil pessoal.
O piloto existente permanece separado. A produção continua restrita à porta
Tailscale existente; broker não publica portas.

## Execução e aceite

- [x] Testar o contrato de Compose opt-in e a supressão do host interno.
- [x] Implementar imagem/perfil e documentar ativação, saúde e reversão.
- [x] Construir imagens e validar aplicação/broker com volume temporário.
- [ ] Revisão independente, PR, CI e merge das adaptações necessárias.
- [ ] Registrar imagem/configuração antigas e criar backup privado consistente.
- [ ] Atualizar stack Komodo, ativar perfil e configuração, preservar login.
- [ ] Validar saúde, conta, Web/CLI, persistência e um turno real isolado.
- [ ] Registrar commit, imagens, backup, CI e limites no aceite de produção.

Reversão: desligar o perfil externo, restaurar configuração anterior e executar
a imagem anterior preservada. Não apagar volumes ou substituir banco ativo
automaticamente por backup; eventual restauração deve preservar dados novos.

## Validação anterior à integração

CI local: 1.558 testes, 5.562 subtests; 12 skips e 23 deselecionados.
Imagem s6: 22 testes e 23 subtests. Workers Docker offline: 14 testes.
Healthcheck e Compose, após correção do cleanup: 10 testes; Ruff passou.
Os frontends não tinham node_modules local e serão verificados na CI remota.

Smoke com volume temporário: aplicação e broker saudáveis, comunicação por
UID/socket compartilhado, API Web autenticada, criação/encerramento de sessão
read_only e parada/reinício gracioso do broker. Nenhum login real foi usado.
A revisão independente aprovou a implementação; a documentação foi corrigida
para não prometer checkpoint novo durante parada de turno ativo.

A imagem de worker registrada no piloto não estava mais no daemon. A imagem
reconstruída e validada será fixada pelo ID no aceite da produção.
