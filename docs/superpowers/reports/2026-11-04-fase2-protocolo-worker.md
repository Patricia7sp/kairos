# E0.2 — protocolo de verificação do worker no Mac

Complementa a [matriz do destino](2026-11-02-fase2-plataforma.md). Objetivo: verificar o endpoint usado pelo launcher e executar um teste existente com fixture sintética, preservando recusas. Não altera política, não implementa adaptação e não encerra E0.3.

## Artefato e pré-requisitos

Usar checkout isolado e limpo de `33d95d38ebf3500404e74ac1d19a22a21581eb2c`, sem descartar mudanças de outro checkout. Os inputs de produto, lock e teste desse SHA são idênticos à base `c6a083f`; os commits intermediários alteram somente documentação. Registrar qualquer outro SHA como outro ensaio. A localização do repositório no Mac ainda precisa ser informada pela usuária; os blocos abaixo pressupõem o diretório raiz desse checkout.

São necessários Git, uv, Python 3.11 e Docker Desktop funcionando. Preparar o ambiente e construir a imagem instala dependências de desenvolvimento no checkout e baixa os artefatos do Dockerfile; essas etapas não são somente leitura. Não iniciar o teste se alguma preparação falhar. Não executar contra projeto pessoal: o teste cria seu próprio diretório temporário sintético.

## 1. Identidade e endpoint — leitura

O [launcher](../../../kairos_runtime/experimental/docker_worker.py) usa configuração temporária vazia e `--host unix:///var/run/docker.sock`, sem depender do contexto selecionado. Ter sucesso com `desktop-linux` não prova acesso por esse endereço. Registrar saída e código de cada consulta:

```bash
date -u '+%Y-%m-%dT%H:%M:%SZ'
git rev-parse HEAD
git status --porcelain
shasum -a 256 uv.lock docker/external-sandbox/Dockerfile kairos_runtime/experimental/worker_policy.py kairos_runtime/experimental/docker_worker.py
docker --host unix:///var/run/docker.sock version --format 'engine={{.Server.Version}}'
printf 'exit_endpoint=%s\n' "$?"
docker --host unix:///var/run/docker.sock info --format '{{json .SecurityOptions}}'
printf 'exit_capacidades=%s\n' "$?"
```

Se o SHA divergir, o checkout estiver sujo ou o endpoint falhar, parar antes de build/worker. Registrar a divergência; não criar symlink de socket, mudar permissões ou trocar endpoint no produto como correção implícita. A ausência de AppArmor nas capacidades já é lacuna; o ensaio seguinte, se executado, é diagnóstico de aceitação/recusa, não autorização de uso.

## 2. Preparação — somente após o endpoint responder

```bash
uv sync --frozen --extra dev --python 3.11
```

Após sucesso, construir a receita existente. O Dockerfile verifica o tarball Codex 0.154.0 por SHA256. A imagem-base usa tag: registrar o ID resultante, sem alegar build bit a bit reproduzível apenas por usar o mesmo Dockerfile. Se a tag local `kairos:external-sandbox` já tiver outro uso, reservar checkout/daemon de teste antes de sobrescrevê-la.

```bash
docker --host unix:///var/run/docker.sock build -f docker/external-sandbox/Dockerfile -t kairos:external-sandbox .
```

Após build concluído com sucesso:

```bash
docker --host unix:///var/run/docker.sock image inspect kairos:external-sandbox --format '{{.Id}}'
docker --host unix:///var/run/docker.sock container ls -a --filter label=io.kairos.external-sandbox=prototype --format '{{.Names}}'
```

## 3. Um teste real, com recusa preservada

O teste [test_real_codex_executes_with_standard_docker_isolation](../../../tests/test_external_sandbox_integration.py) cria um worker descartável, valida WorkerPolicy e evidência do kernel e só então executa comandos sintéticos. Verifica UID 10000, capacidades zeradas, no-new-privileges, seccomp ativo, AppArmor enforce, limites de cgroup, rootfs somente leitura, isolamento de arquivos e rede. Não usa credenciais/modelo pago. A tentativa de conexão externa do teste ocorre dentro do worker com rede none e deve falhar.

```bash
KAIROS_EXTERNAL_SANDBOX_TEST=1 KAIROS_RUNTIME_SANDBOX_TEST=0 KAIROS_WORKER_SANDBOX=0 uv run --frozen --extra dev --python 3.11 python -m pytest -vv -x -rs tests/test_external_sandbox_integration.py::test_real_codex_executes_with_standard_docker_isolation
printf 'exit_teste=%s\n' "$?"
docker --host unix:///var/run/docker.sock container ls -a --filter label=io.kairos.external-sandbox=prototype --format '{{.Names}}'
printf 'exit_verificacao_residuos=%s\n' "$?"
```

Registrar o resumo do teste e erro sanitizado, os códigos, ID da imagem e a diferença entre nomes de workers antes/depois. Não enviar ambiente completo ou configurações pessoais. Qualquer novo worker residual é falha de encerramento a investigar; não remover containers alheios ou executar prune.

## Critérios de interpretação

- Falha de endpoint, imagem ou dependência: preparação indisponível; o isolamento não foi exercitado.
- Recusa por AppArmor/Seccomp/WorkerPolicy: perfil incompatível ou divergente no destino. Preservar o erro e manter o perfil indisponível; falha do teste não vira sucesso de compatibilidade.
- Skip/deseleção, zero testes ou timeout externo: evidência insuficiente. Não registrar aprovação.
- Teste passou, sem novos resíduos: evidência apenas desse cenário no SHA/daemon/imagem registrados. Ainda faltam os demais cenários de cancelamento/recuperação, filesystem e E0.3.

Não habilitar `KAIROS_RUNTIME_SANDBOX_TEST=1` neste ensaio: o perfil sandbox interno exige provisionamento e design próprios. A ausência de equivalência comprovada requer decisão revisada sobre adaptação ou backend autorizado; este protocolo não escolhe infraestrutura alternativa.

## Ensaio do procedimento no Linux — 04/11/2026

Verificação de encerramento registrada às `2026-11-04T00:24:08Z`. Produto do checkout `33d95d3`, sem mudanças de código, teste, dependências ou configuração. Docker Engine `29.7.2`, capacidades declaradas AppArmor, seccomp builtin e cgroupns. Build da receita existente concluído, com camadas reutilizadas do cache; imagem local resultante `sha256:2a4b5677bffd5a48859a8c1fe85eb5a1f3626669fb7e6f8c3f3d37bedcf607a2`.

- `python -m pytest -q tests/test_external_sandbox.py`: 42 passed em 0,78 s, exit 0.
- Teste real do bloco 3, com os mesmos três opt-ins e nó exato: 1 passed em 1,85 s, exit 0. Usado o Python 3.11.16 do venv de desenvolvimento já preparado; confirmado o import de `docker_worker.py` deste checkout. O bootstrap uv não foi repetido nesse venv.
- Listagem dos workers com label `io.kairos.external-sandbox=prototype` vazia antes e depois, ambas com exit 0; nenhum worker residual observado.

Esses resultados validam os caminhos testados no Linux. Não são execução no Mac, teste de todos os cenários de isolamento, benchmark E0.3 ou aprovação de portabilidade. O caminho de recusa no Docker Desktop continua sem resultado real até o ensaio no destino.
