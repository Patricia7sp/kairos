# Diagnóstico da sandbox Codex no Docker

**Somente diagnóstico. Não conectar estes perfis ao compose de produção.**
Eles permitem investigar as primeiras barreiras do bwrap; não constituem uma
política completa, validada ou aprovada para execução de agentes.

## Origem

Docker Engine 29.7.2, commit `6a43e3d`, amd64. Arquivos upstream:

- [seccomp/default.json](https://github.com/moby/moby/blob/6a43e3d/vendor/github.com/moby/profiles/seccomp/default.json)
- [apparmor/template.go](https://github.com/moby/moby/blob/6a43e3d/vendor/github.com/moby/profiles/apparmor/template.go)
- [apparmor/apparmor.go](https://github.com/moby/moby/blob/6a43e3d/vendor/github.com/moby/profiles/apparmor/apparmor.go)

Licença Apache 2.0 preservada em `LICENSE.moby`.
O SHA-256 do JSON original é
`536529b665dd0972c37bfb569f5d4ac8a53592e7b00752bc39ff063ca9864c74`.

`seccomp-probe.json` preserva integralmente as regras originais e acrescenta
duas permissões observadas pelo strace no Codex CLI 0.153.4:

| Syscall amd64 | Restrição de argumento | Finalidade |
| --- | --- | --- |
| `clone` | argumento 0 = `0x78020011` | NEWNS, NEWIPC, NEWUSER, NEWPID, NEWNET e SIGCHLD |
| `mount` | argumento 3 = `0x8c000` | MS_REC, MS_SILENT e MS_SLAVE |

`clone3` continua com fallback ENOSYS. Não foram permitidos `unshare`, `setns`
ou outros flags de mount. Não usar o perfil em outra arquitetura ou versão
sem nova análise. O seccomp não consegue restringir os caminhos apontados por
argumentos de mount; a regra de caminho correspondente fica no AppArmor.

`apparmor-probe` é o template oficial materializado como perfil independente
`kairos-runtime-diagnostic`, ABI 3.0, com os includes de tunables/global e
abstractions/base. Substitui somente `deny mount` por permissão de propagação
recursiva slave sobre `/`. As demais operações de mount continuam negadas por
ausência de allow. As negações de `/proc`, `/sys` e AF_ALG são preservadas.
Não altera `docker-default` nem configura o daemon.

## Evidência de 2026-09-06

Ambiente: kernel 7.0.0-29-generic, Docker 29.7.2, Codex 0.153.4;
`unprivileged_userns_clone=1`, `apparmor_restrict_unprivileged_userns=1`.
Os testes usaram containers descartáveis, UID 10000, `--network none`, sem
bind mounts ou volume de produção, sem credenciais e sem capabilities extras.

1. Perfil padrão: `clone(...CLONE_NEWUSER...)=EPERM`; bwrap não cria namespace.
2. Exceção seccomp apenas para clone: clone passa; mount de propagação retorna
   EPERM. Logo, a primeira barreira era o seccomp, não o sysctl sugerido pela
   mensagem genérica do bwrap.
3. Duas exceções seccomp: o mesmo mount retorna EACCES. O AppArmor padrão
   contém `deny mount`. Não houve registro correspondente no journal
   consultado; negações explícitas de AppArmor podem ser silenciosas.
4. Teste negativo com `seccomp-probe.json`: unshare só de mount, setns e mount
   com outros flags continuam retornando EPERM.
5. Parser AppArmor (`-Q -T`) aceita `apparmor-probe`. A carga no kernel ainda
   não foi executada: `sudo -n apparmor_parser -r -W ...` devolveu
   `sudo: interactive authentication is required`.

## Próximo teste, com intervenção administrativa

Para reproduzir o rastreamento, construir uma imagem separada com `strace`:

```bash
docker build -f docker/sandbox/diagnostic/Dockerfile.debug \
  -t kairos:sandbox-debug docker/sandbox/diagnostic
```

No comando do probe abaixo, trocar `kairos:local` por `kairos:sandbox-debug`
e prefixar `codex sandbox` com
`strace -f -e trace=clone,clone3,unshare,setns,mount,umount2,pivot_root,chroot,prctl,seccomp`.

Revisar o perfil e carregá-lo no host (não dentro do container):

```bash
sudo apparmor_parser -r -W docker/sandbox/diagnostic/apparmor-probe
```

A cópia idêntica oferecida ao operador nesta sessão está em
`/tmp/kairos-runtime-diagnostic.apparmor`, SHA-256
`2a3faa617ee1760e555e469d3600914f8aa8f00e80b5dcd012c9158b5b7a2c46`.

Executar somente o probe inofensivo, sem volumes de produção:

```bash
docker run --rm --network none --user 10000:10000 \
  --security-opt seccomp=./docker/sandbox/diagnostic/seccomp-probe.json \
  --security-opt apparmor=kairos-runtime-diagnostic \
  --entrypoint /bin/sh kairos:local -c \
  'mkdir -m 700 /opt/data/probe; CODEX_HOME=/opt/data/probe codex sandbox /bin/true'
```

Este perfil só cobre as duas primeiras operações observadas; outras chamadas
necessárias ao bwrap podem continuar bloqueadas. Cada nova permissão exige
evidência, restrição e revisão. Não usar `privileged`, SYS_ADMIN, perfis
unconfined ou alteração global de sysctl como atalho.

Ao terminar os testes, após encerrar todos os containers que usam o perfil:

```bash
sudo apparmor_parser -R docker/sandbox/diagnostic/apparmor-probe
```

## Critérios antes de qualquer aplicação em produção

- Probe da sandbox executa com UID 10000, sem capabilities extras.
- `workspace_write` grava somente no projeto autorizado; `read_only` não grava.
- Escrita fora do projeto, rede proibida e acesso indevido a outros projetos
  continuam bloqueados. O teste de rede precisa de um controle fora da sandbox
  que consiga conectar: `--network none` sozinho não prova isolamento do Codex.
- Perfis finais mínimos, com revisão, testes de regressão e rollback definido.
- Credencial dedicada e aceite real de turnos, aprovação e recuperação.

Nenhuma configuração de produção foi alterada nesta investigação.
