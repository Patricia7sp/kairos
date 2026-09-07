# Aceite do protótipo de sandbox externa

Data: 2026-09-07. Branch: `feat/runtime-external-sandbox`, baseada em `46c4087`.
Aceite restrito ao protótipo offline e ao ambiente de desenvolvimento; não é
aceite de habilitação do runtime em produção.

## Resultado

Codex 0.153.4 executou `command/exec` com `externalSandbox` e rede restricted
em Docker padrão, sem novo perfil AppArmor, sem sudo, sem autenticação e sem
chamadas de modelo. O teste CLI imprimiu `42` e confirmou remoção do container.
O relatório bruto está em `2026-09-07-external-sandbox-evidence.json`.

Imagem worker: `sha256:bad209be2735d28a6ea4bed73cf028478fdd568b1a0693e15cd26154fe5481df`.
Imagem development: `sha256:d9df2e18407a0e1d94abf7fe25543edfa847fd79585f75cd419d3eb1aad830bc`.

Proteções observadas: UID 10000, CapEff/CapPrm/CapBnd zerados, NoNewPrivs=1,
Seccomp=2, `docker-default (enforce)`, apenas interface loopback, memória
536870912 bytes, swap adicional zero, pids.max=128 e cpu.max=100000/100000.

## Validação

- Host: **1.404 testes Python aprovados**, 9 ignorados por opt-in Docker externo,
  22 deselecionados (21 de imagem de produção + 1 live), 5.561 subtests.
- Sandbox externa: **9 testes reais Docker aprovados**. Cobrem execução,
  rootfs e host inacessíveis, bloqueio TCP por IP, proteção read_only contra
  chmod/rename, separação de dois workers, timeout retornado pelo servidor,
  cancelamento de comando e nas fases create/import/initialize, e tentativa de
  interferência na atestação por módulos Python presentes no projeto.
- Unitários do protótipo: **33 aprovados**, incluídos também na suíte do host.
- Suíte Python dentro do Dev Container, com rede desligada e projeto em volume
  anônimo: **1.400 aprovados**, 10 ignorados, 25 deselecionados e 5.552 subtests.
  As três verificações adicionais deselecionadas exigem Docker no host e estão
  cobertas pela suíte do host. Compilação nativa também aprovada no container.
- Frontend dentro do Dev Container: **93 testes aprovados** (58 Web, 17 TUI,
  18 desktop), além do typecheck Web.
- Imagens worker/development construídas; wheel e sdist construídos; JSON do
  Dev Container válido; ruff check/format, shellcheck e git diff --check aprovados.
  Hadolint sem avisos bloqueadores; informação DL3066 em `USER root` de build.
- Revisão independente sem pendências materiais após as correções. O teste
  anterior de heartbeat foi estabilizado para observar perda após dispatch,
  mantendo as verificações de interrupção, dispatch único e quarentena.

Falhas reproduzidas e corrigidas antes do aceite: import de módulos do projeto
durante atestação, abandono de create após cancelamento, falta de limpeza local
quando remoção falha, e timeout de servidor retornado sem destruir o worker.

## Estado e limites

Nenhum worker experimental permaneceu após os testes. A produção continuou
healthy, em `docker-default`, com a mesma imagem
`sha256:5d779e2b7d2d42d4d107cae68a92d676fc03a61d26254a60c13a0410e5d65f24`.
O Compose e os módulos de produção não foram alterados. Os perfis AppArmor
diagnósticos anteriores não foram usados nem recarregados nesta etapa.

O Dev Container usa cópia em volume próprio; o worker usa tmpfs descartável.
Alterações não são exportadas automaticamente. O launcher permanece no host,
e verificações que exigem Docker são executadas no host. A extensão do editor
pode ter compartilhamentos pessoais de autenticação; ela não faz parte da
atestação de segurança do worker. A abertura pela interface do VS Code não foi
testada nesta sessão; foram testadas a imagem e a montagem equivalente de volume.

O build `npm ci` sinalizou 5 vulnerabilidades nas dependências frontend existentes
(3 moderadas, 1 alta e 1 crítica). Os lockfiles não foram alterados; atualizar
essas ferramentas é uma pendência separada. Nenhuma porta de servidor de
desenvolvimento foi publicada nos testes.

## Próxima etapa arquitetural

Integrar um broker restrito de workers ao host do Kairos, roteamento por sessão,
persistência/retomada, exportação revisável e autenticação de modelo com egress
controlado. Não montar Docker socket no agente nem liberar rede geral como atalho.
Validar turn/start e todos os fluxos de aprovação/cancelamento/retomada: o teste
de comandos não demonstra integração de threads no adaptador de produção.
Manter o runtime desabilitado até esse aceite adicional. Nenhum merge ou deploy
desta branch foi realizado nesta etapa.
