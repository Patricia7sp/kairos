# Triagem de arquivos, configuração e credenciais

Base: `08b213f4c74f72f2162d9db11adca83ef0c93635`, após o merge e deploy do
PR #125. Escopo: três fluxos do inventário Sonar da análise
`483751c9-6dc4-4623-bb59-295d61abc91f`, com 71 alertas de segurança/confiabilidade.
Plano: [fronteiras de arquivos](../plans/2026-10-31-fronteiras-arquivos.md).
Nenhuma classificação remota foi modificada nesta triagem.

## Resultado por fluxo

| Alerta | Caminho investigado | Resultado e limite |
| --- | --- | --- |
| `AaE_nNi3A4HaqsrPZgCS`, S2083 | `read_text → edit_file_tool → write_text` | O dado marcado é o conteúdo substituído; não define o argumento de caminho. Testes reais de edição preservam o destino e a sentinela externa, com e sem workspace. Os testes existentes exercitam recusas de escape e trocas de symlink. Candidato a falso positivo deste fluxo específico; não é atestação de todo acesso a arquivos. |
| `AaFDIjVi91Ko4sQxGKfx`, S8707 | CLI experimental `--config-home → load_execution_limits` | `--config-home` é seleção explícita do operador no host; o loader procura o basename fixo `config.yaml`. A composição do Chat usa o home do serviço, não um argumento de ferramenta fornecido pelo modelo. Traversal não reproduzido nesse fluxo. A investigação reproduziu duas falhas associadas, corrigidas abaixo. |
| `AaFDIk1r91Ko4sQxGKfy`, S6549 | conteúdo de `cron/jobs.json → credential_file_lock` | O caminho do JobStore vem do home de construção, não de um campo do job. Reabertura real com `path` absoluto, relativo, NUL e ciclo de link conserva o arquivo e o lock do home, sem tocar os alvos externos. Candidato a falso positivo deste fluxo; não audita todos os consumidores da primitiva de lock. |

Fontes consultadas: [API de issues do SonarCloud](https://sonarcloud.io/api/issues/search?componentKeys=Patricia7sp_kairos&branch=main&types=BUG%2CVULNERABILITY&resolved=false&ps=500),
`kairos_tools/workspace.py`, `kairos_tools/builtin.py`,
`kairos_runtime/experimental/__main__.py`, `kairos_integration/chat_sandbox.py`,
`kairos_cron/jobs.py` e `kairos_security/credentials/io.py`.
São conclusões da leitura e dos experimentos delimitados, não alegação de
ausência universal de vulnerabilidades ou de aprovação pelo scanner.

## Falhas reproduzidas e correção

`load_execution_limits` tratava `config.yaml` existente não regular como se
estivesse ausente, retornando os padrões. Diretório, FIFO, link quebrado e
ciclo de link reproduziram esse caminho. Isso violava D-RT.3: configuração
presente inválida deve recusar a fronteira, sem aceitar limites presumidos.
O construtor real de `ChatBashSandbox` também aceitava o diretório.

O loader agora distingue a ausência da entrada das falhas posteriores,
abre um descritor não bloqueante, comprova arquivo regular por `fstat` e lê
pelo mesmo descritor. `finally` fecha o recurso em sucesso e erro. O FIFO é
exercitado em subprocesso com timeout, sem depender de um escritor. Aliases
para arquivos regulares continuam aceitos; ausência continua selecionando
os padrões existentes.

Outra reprodução mostrou um canário de configuração na mensagem e no
traceback de YAML malformado. O nome de uma chave desconhecida também era
reproduzido no erro. UTF-8 inválido escapava sem a categoria de diagnóstico
do loader. Agora falhas de I/O, decodificação e YAML recebem erro nomeado
com classe segura e causa suprimida; chaves arbitrárias não são ecoadas.
As regressões verificam mensagem e traceback formatado, sem usar segredos
reais. A CLI experimental já oferecia mensagem genérica; não foi demonstrado
um exploit remoto ou vazamento por aquela saída pública.

As mudanças preservam as assinaturas e o formato dos limites. Não há nova
restrição ao home escolhido pelo operador, promessa de imutabilidade contra
outro processo do mesmo UID, migração, dependência ou alteração de política
de credenciais/workspace.

## Evidências

- Baseline direcionada: 98 testes aprovados, 3,85 s.
- RED antes de alterar produção: nove falhas e um positivo aprovado. As
  falhas demonstraram defaults para configurações presentes inválidas,
  conteúdo no diagnóstico e aceitação pelo construtor da sandbox.
- GREEN integrado inicial: 132 testes aprovados, 4,34 s, incluindo loader,
  sandbox do Chat, despacho de ferramentas, cron e os novos fluxos.
- Ruff check/format e diff check direcionados aprovados.
- Evidências locais em `.superpowers/evidence/fronteiras-arquivos-20261031/`.
  A primeira baseline na sandbox ficou aguardando em código assíncrono e foi
  interrompida; a repetição com permissões para os subprocessos locais passou.
  A tentativa interrompida não é atribuída como aprovação.
- Revisão independente, CI integral e validação na imagem ainda pendentes
  nesta versão do relatório. Os resultados finais serão registrados antes
  de preparar o PR.

## Continuidade

O PR #125 foi squash-merged em `08b213f`, com
[CI da main e deploy aprovados](https://github.com/Patricia7sp/kairos/actions/runs/41221925272).
O checkout do Komodo foi conferido nessa revisão; aplicação/broker healthy,
readiness e smoke HTTP 200/`status: ok` aprovados. Os 338 módulos Python
conferidos em cada container coincidiram por SHA-256. Esse é o fechamento
da entrega anterior, não uma implantação desta correção.

A curadoria continua sem escopo escolhido pela usuária e fora deste lote.
Cancelamento/erros assíncronos e a investigação operacional dos erros de I/O
anteriores permanecem acompanhamentos separados. As classificações acima
não encerram automaticamente o restante do inventário Sonar.
