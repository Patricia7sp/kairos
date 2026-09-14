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
recursiva slave com o flag silent sobre `/`. As demais operações de mount continuam negadas por
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
5. O operador carregou o perfil AppArmor em 2026-09-06T22:33:29Z. Containers
   descartáveis confirmaram `kairos-runtime-diagnostic (enforce)`. O probe
   continuou falhando, com e sem strace.
6. O journal confirmou `operation="mount"`, `info="failed flags match"`,
   destino `/` e flags `rw, silent, rslave`: faltava `silent` na regra inicial.
   A regra foi corrigida para corresponder aos flags exatos, sem ampliar
   destinos. Após a recarga pelo operador, esse mount passou. O próximo
   bloqueio foi `mount("tmpfs", "/tmp", "tmpfs", MS_NOSUID|MS_NODEV, NULL)`,
   com EPERM. A imagem de strace havia sido removida e foi reconstruída a
   partir de `kairos:local`, sem alterar a imagem ou o container de produção.

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
`38eb47263c8ee1b696cfe096680729b055a603dd352d2785499dd4eea71669da`.

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

## Candidato da sequência de montagem — ainda não validado

Para reduzir recargas administrativas, `apparmor-candidate` e
`seccomp-candidate.json` cobrem a sequência de montagem derivada do código
oficial de [bubblewrap.c](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/vendor/bubblewrap/bubblewrap.c)
e [bind-mount.c](https://github.com/openai/codex/blob/rust-v0.153.4/codex-rs/vendor/bubblewrap/bind-mount.c).
Esses arquivos são candidatos de diagnóstico, não substitutos aprovados dos
perfis de produção. A revisão estática não prova compatibilidade no kernel.

O seccomp original continua intacto, com 12 exceções adicionais:

- Clone exato do primeiro namespace, como no probe mínimo.
- Mounts com flags exatos para propagação slave, tmpfs, proc, devpts, bind
  inicial (incluindo o marcador legado MS_MGC_VAL), rbind e propagação private.
- Remount com máscara que exige BIND, REMOUNT, SILENT e NOSUID; somente
  RDONLY, NODEV, NOEXEC, NOATIME, NODIRATIME e RELATIME podem variar.
- `pivot_root`, com caminhos limitados no AppArmor.
- `umount2` somente com MNT_DETACH.
- `unshare` somente de CLONE_NEWUSER: o Codex usa `--dev`, cujo devpts requer
  UID0 no primeiro user namespace; bwrap cria o segundo para restaurar UID10000.
  Não se permite unshare de mount, rede, PID ou combinações adicionais.

No AppArmor, as permissões de montagem ficam nos caminhos temporários de
construção `/tmp`, `/oldroot` e `/newroot`; tmpfs, proc e devpts têm tipos e
flags restritos. Os dois pivot_root seguem os pares de caminhos da fonte.
As combinações de remount estão enumeradas e sempre exigem nosuid.

**Limitação identificada na revisão:** as permissões valem para todos os
processos do perfil, não somente para bwrap. Binds podem criar aliases; as
negações herdadas para `/proc` e `/sys` não cobrem automaticamente caminhos
alternativos sob `/oldroot` ou `/newroot`. Portanto este candidato não
sustenta equivalência ao confinamento docker-default e não deve ser ligado
ao serviço Kairos. O desenho final precisa de revisão de isolamento além de
um probe funcional; VM dedicada permanece uma alternativa se a política do
container não puder ser restringida adequadamente.

Validação antes da carga: AppArmor aceita pelo parser; seccomp aceito pelo
Docker. Sete testes negativos retornaram EPERM: unshare de mount, unshare
combinando user+mount, setns, mount sem flags permitidos, remount sem nosuid,
umount forçado e bpf. O teste usa AppArmor mínimo já carregado e o seccomp
candidato; não prova que a sequência completa funciona.

Revisar e carregar o novo perfil, separado do anterior:

```bash
sudo apparmor_parser -r -W /tmp/kairos-runtime-candidate.apparmor
```

Cópia versionada: `docker/sandbox/diagnostic/apparmor-candidate`.
SHA-256 do AppArmor candidato:
`61f5cb192e494e10cdaf50a6fac5d652c4ba7001aa89c75eace00113d9497d8c`.
SHA-256 do seccomp candidato:
`c4e2537ba74df4a282b7e2b0c1ea38de36b8f3ed39514412ba6b4fb13df9f6df`.

Depois da confirmação administrativa, repetir o probe usando
`--security-opt apparmor=kairos-runtime-candidate` e
`--security-opt seccomp=./docker/sandbox/diagnostic/seccomp-candidate.json`.
Para os negativos, dentro de um container descartável UID10000 com os mesmos
perfis, executar `check-negative-syscalls.py` pela entrada padrão do Python.
O script recusa execução fora de amd64, UID10000, seccomp ativo e perfil
de diagnóstico em enforce, ou com capabilities efetivas.

Ao terminar, encerrar os containers de teste e remover apenas este perfil:

```bash
sudo apparmor_parser -R /tmp/kairos-runtime-candidate.apparmor
```

### Primeiro teste do candidato carregado

Após a carga pelo operador, o probe passou pelas quatro primeiras operações:
propagação slave, tmpfs em `/tmp`, bind inicial e primeiro pivot_root. Falhou
ao montar a raiz de destino: tmpfs em `/newroot/` e, na tentativa de fallback
do Codex, bind de `/oldroot/` para `/newroot/`. O journal confirmou as negações
no perfil candidato; nenhum desses probes chegou a executar `/bin/true`.

O dump `apparmor_parser -Q -T -D rule-exprs` identificou a causa: nesta versão
do parser, `/newroot/**` expande para um padrão que exige ao menos um caractere
depois de `/newroot/`. A raiz em si não era coberta; o mesmo ocorria com a
origem `/oldroot/**`. As regras foram corrigidas para `/newroot/{,**}` e
`/oldroot/{,**}`, incluindo explicitamente as raízes, sem adicionar caminhos
fora da área de construção.

O kernel apresenta binds com apenas MS_BIND e MS_REC; isso foi confirmado no
[aa_bind_mount do Linux 7.0](https://github.com/torvalds/linux/blob/v7.0/security/apparmor/mount.c).
O compilador do AppArmor já normaliza esses flags ao gerar as expressões.
As regras de flags não precisaram ser ampliadas para corrigir essa falha.

`check-apparmor-expressions.py` consulta o compilador, sem carregar perfil no
kernel. O teste reproduziu a falha antes da mudança e passou após a correção:
138 requisições permitidas (incluindo raiz, descendants e combinações de
remount) e sete requisições negadas. Ele verifica as expressões antes da
aplicação da DFA final, não substitui teste de isolamento no kernel e não
comprova a sequência completa de pivot_root/umount.

```bash
python3 docker/sandbox/diagnostic/check-apparmor-expressions.py
```

Ruff lint/format e diff-check passaram. A cópia atualizada em
`/tmp/kairos-runtime-candidate.apparmor` está pronta para recarga administrativa.
Não houve alteração de seccomp nesta correção nem modificação da produção.
