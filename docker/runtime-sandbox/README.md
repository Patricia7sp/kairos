# Sandbox interna do worker (bwrap via `codex sandbox`)

Perfis de runtime para o worker habilitar a **sandbox interna** do Codex dentro
do container. Sem esta camada, o worker roda com `docker-default` / `cap-drop
ALL` / `no-new-privileges` e o bwrap é interrompido na primeira barreira
(`clone(...CLONE_NEWUSER...)` com EPERM pelo seccomp padrão).

**Somente ative com a camada de host pronta** (seccomp disponível ao daemon e
perfil AppArmor carregado no host). Os passos abaixo são executados na máquina
que administra o Docker — não dentro de um container, e nunca dentro do worker.

## Artefatos

- `seccomp-runtime.json` — perfil seccomp do worker (derivado de
  `docker/sandbox/diagnostic/seccomp-probe.json`, que preserva o
  `default.json` do Moby 29.7.2 com SHA-256 documentado e acrescenta apenas as
  exceções observadas por strace: `clone` com flags de namespace e o `mount`
  de propagação `MS_REC|MS_SILENT|MS_SLAVE`). O `clone3` continua ENOSYS.
- `apparmor-kairos-worker-runtime` — perfil AppArmor do worker (template
  materializado do Moby, ABI 3.0) substituindo somente `deny mount` pela
  propagação `mount options=(rw, silent, rslave) -> /`. Demais montagens
  continuam negadas por ausência de allow; `/proc`, `/sys` e AF_ALG seguem
  bloqueados.

## Pré-requisitos de host

1. Publicar o perfil seccomp em caminho visível ao daemon (padrão
   `/opt/kairos/seccomp-runtime.json`, sobrescrevível por
   `KAIROS_WORKER_SECCOMP_PATH`):

   ```bash
   sudo install -D -m 0444 docker/runtime-sandbox/seccomp-runtime.json \
     /opt/kairos/seccomp-runtime.json
   ```

2. Carregar o perfil AppArmor **no host** (o worker só referencia o nome
   `kairos-worker-runtime`):

   ```bash
   sudo apparmor_parser -r -W docker/runtime-sandbox/apparmor-kairos-worker-runtime
   # conferir: sudo aa-status | rg kairos-worker-runtime
   ```

3. Conferir os sysctls que o bwrap usa para seção de usuário sem privilégio
   (o diagnóstico usou `unprivileged_userns_clone=1`). Se o host restringe
   namespaces de usuário, o bwrap falhará mesmo com os perfis corretos.

4. Testar sem tocar em produção:

   ```bash
   KAIROS_WORKER_SANDBOX=1 uv run python -m kairos_runtime.experimental \
     --project /caminho/projeto-de-teste -- bash -c 'codex sandbox /bin/true'
   ```

   A atestação do worker falha fechado se o daemon não aplicar `seccomp=`
   e `apparmor=kairos-worker-runtime` exatamente como pedido.

   Com a camada de host pronta, o teste de integração opt-in:

   ```bash
   KAIROS_EXTERNAL_SANDBOX_TEST=1 KAIROS_RUNTIME_SANDBOX_TEST=1 \
     uv run pytest -q tests/test_external_sandbox_integration.py::test_sandbox_mode_worker_attests_runtime_profiles
   ```

   Evidência local desta séries (kernel 7.0.0-29-generic, Docker 29.7.2):
   no seccomp padrão o `codex sandbox` termina em `bwrap: No permissions to
   create a new namespace`; com `seccomp-runtime.json` o namespace passa e a
   falha muda para `Failed to make / slave: Permission denied` — a barreira do
   AppArmor `deny mount`, removida somente com o perfil carregado no host.

## Ativação

A sandbox interna é opt-in por flag (`--sandbox`) ou ambiente
`KAIROS_WORKER_SANDBOX=1`. O padrão continua o protótipo atual
(`docker-default`, sem sandbox interna), mantendo o comportamento validado.

## Limitação conhecida e critérios de produção

O diagnóstico documentou a barreira seguinte após a propagação de mount: um
`mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID|MS_NODEV, NULL)` com EPERM. Perfis
finais mínimos exigem **evidência, restrição e revisão** por host — conforme
`docker/sandbox/diagnostic/README.md`. Critérios antes de ampliar:

- Probe da sandbox executa com UID 10000, sem capabilities extras.
- `workspace_write` grava somente no projeto autorizado; `read_only` não grava.
- Escrita fora do projeto, rede proibida e acesso indevido a outros projetos
  continuam bloqueados.
- Perfis finais mínimos, com testes de regressão e rollback definido.

## Rollback

- Desative a flag/ambiente (`KAIROS_WORKER_SANDBOX` desligado) — nenhum worker
  novo usa os perfis.
- Descarregue o perfil AppArmor após encerrar todos os containers que o usam:

  ```bash
  sudo apparmor_parser -R docker/runtime-sandbox/apparmor-kairos-worker-runtime
  ```

- Os perfis não alteram `docker-default` nem configuram o daemon.