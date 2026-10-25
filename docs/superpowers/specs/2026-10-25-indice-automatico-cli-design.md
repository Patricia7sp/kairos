# Índice automático de skills no Chat da CLI

Status: desenho aprovado e execução autorizada pela usuária em 25/10/2026
(“aprovado, pode seguir”). Implementação em fechamento no PR #119, conforme o
[plano](../plans/2026-10-25-indice-automatico-cli.md); merge e deploy não estão
autorizados por essa aprovação. Base de runtime examinada:
`89020109ad1d063f06dba9b685681e15cb22590a`, já implantada. O fechamento
documental do ledger está no PR #118, separado deste recorte.

## Intenção e escopo

Permitir que o modelo descubra skills instaladas durante o Chat da CLI sem
repetir uma opção a cada turno. O primeiro incremento cobre `run` e `chat`,
em mensagem única e no modo interativo existente. A CLI oferece uma opção
para desligar o índice. Corpos e referências continuam sendo lidos sob demanda.

A usuária escolheu a CLI entre os recortes apresentados. As decisões sobre
três modos, tratamento de ausência, interfaces internas e observabilidade
abaixo são propostas técnicas do Root para tornar esse recorte implementável.

Ficam fora Web, TUI/desktop, gateways, ACP e Agent Runtime, além de `/learn`,
curadoria, sincronização, instalação, execução de scripts e alteração de
proveniência. Nenhuma validação de ledger, política ou identidade é dispensada.

## Rastreabilidade e base existente

| Fonte | Contrato preservado | Aplicação |
| --- | --- | --- |
| `_reversa_sdd/skills/requirements.md`, unit skills, RF-09 | Índice compacto, cache e snapshot estável | Reusar manifesto e snapshot SQLite; automatizar a ativação na CLI |
| Mesma referência, RF-17; `skills/tasks.md`, T-11/T-13 | Só índice no prompt; referências sob demanda | Reusar `skill_view`, paginação, hashes e limites |
| [Catálogo aprovado](2026-10-08-catalogo-skills-design.md) | Snapshot desde a primeira ativação; leitura limitada ao turno | Preservar formato e persistência; ampliar a ativação para o interativo |
| `kairos_cli/chat.py`, `run_chat` / `_run_turn` / `_run_interactive` | Superfície CLI e separação de runtime | Propagar a escolha por todas as linhas do interativo |
| `kairos_integration/interaction_service.py`, `_stream_owned` | Posse, seleção congelada e capacidade do turno | Resolver automático dentro da posse, antes de enviar ao provider |
| `kairos_integration/skill_catalog_turn.py` e `kairos_state/repositories/skill_catalogs.py` | Snapshot persistido, vencedor único e assets verificados | Reutilização sem alterar schema ou manifestos antigos |
| `kairos_skills/catalog_io.py` | Leitura ancorada e ausência distinguida de erro | Descoberta automática com resultado vazio tipado, sem fallback genérico |

As fontes Reversa ficam em `../hermes-agent/_reversa_sdd/` a partir da raiz
principal. A referência é o contrato identificado acima, não a implementação
do legado por inferência.

## Abordagens consideradas

1. **CLI primeiro — escolhida.** Ativar automaticamente o mecanismo existente
   nos turnos únicos e interativos, com desligamento explícito. Limita a
   mudança de custo e divulgação a uma superfície já capaz de consumir skills.
2. **CLI, Web e gateways juntos.** Ofereceria cobertura maior, mas exigiria
   novos controles e contratos por entrada. Não foi o recorte escolhido.

Dentro da abordagem escolhida, trocar simplesmente `False` por `True` não
resolve o contrato: hoje catálogo vazio e modelo sem ferramentas recusam a
ativação explícita. O modo automático precisa distinguir essas condições de
erros, preservando o chat comum sem disfarçar falhas de segurança.

## Uso e decisão por turno

| Entrada na CLI | Modo | Resultado |
| --- | --- | --- |
| Sem opção de catálogo | `auto` | Ativa quando há modelo apto e catálogo válido não vazio |
| `--skills-catalog` | `on` | Exige catálogo válido e modelo apto; falha se indisponível |
| `--no-skills-catalog` | `off` | Não lê nem anuncia catálogo, não oferece `skill_view` |

Usar o padrão `BooleanOptionalAction` já empregado em `--experiences`, com
valor ausente distinto de desligamento explícito. Repetição de opções segue
o comportamento existente do argparse: a última opção prevalece.

No interativo, o modo escolhido vale para todos os turnos daquela chamada.
Cada linha recebe capacidade e orçamento próprios, mas reutiliza o snapshot
da sessão. Não há novo comando interativo de refresh ou de troca do modo.
`--skill NOME` conserva seu contrato atual, inclusive sua restrição a mensagem
única; não atualiza o catálogo fixado nem passa a funcionar no interativo.

Agent Runtime: `auto` é resolvido como desligado pela CLI antes de montar o
envelope; `on` continua recusado antes da execução; `off` é permitido. Nenhuma
capacidade de Chat é enviada ao runtime.

## Automático: elegibilidade e erros

A seleção de modelo é resolvida uma única vez, dentro da posse do turno, e
congelada para a decisão e a requisição. Não fazer uma seleção na CLI e outra
no serviço que possam divergir.

1. `off`: preservar o caminho atual sem catálogo, sem consultas ao repositório
   do catálogo ou filesystem de skills.
2. `auto` com suporte a ferramentas diferente de `True`: continuar sem
   catálogo, com motivo observável. Não consultar catálogo, assets ou handler
   nativo de leitura. Erro de seleção do modelo continua sendo erro.
3. Modelo apto: conferir o handler nativo sem override e procurar primeiro o
   snapshot existente. Snapshot ou cache incoerente recusa o turno.
4. Sem snapshot: capturar a instalação pelo leitor ancorado existente. Só
   ausência benigna ou vazio genuíno podem produzir catálogo indisponível.
5. Snapshot válido não vazio: ativar exatamente o mecanismo atual. `on`
   conserva a recusa por modelo inapto e por catálogo indisponível.

Ausência benigna significa que o home foi aberto com segurança e somente o
componente final `skills` não existe. Um home inválido, link, componente sem
permissão, erro de I/O, limite excedido ou mudança observada não vira vazio.
Raiz válida sem diretórios candidatos nem omissões é vazio genuíno. Se a
captura não encontra skill válida e registra omissões, recusar em vez de
apresentar a instalação inválida como vazia. Havendo entradas válidas, manter
a política e a contagem de omissões do catálogo atual.

Conservar `capture_skill_catalog` estrita para seus consumidores atuais.
Extrair descoberta compartilhada e um resultado interno tipado para o caso
vazio; não reconhecer condições pelo texto de exceções, nem capturar uma
classe ampla de erro para devolver sucesso. Nomes privados desse resultado
ficam a cargo do implementador.

Falha real na ativação recusa antes de chamar o provider e segue o tratamento
de erro atual da CLI, com exit não zero; no interativo pode encerrar a chamada.
Não prometer continuidade do loop após uma exceção de pré-execução. O usuário
pode voltar à sessão com `--no-skills-catalog`; dados e histórico permanecem.

## Snapshot e compatibilidade de persistência

Não mudar schema, versão do manifesto, tabelas, limites ou dependências.
`SkillCatalogSnapshot` continua exigindo uma ou mais entradas. Catálogos e
assets antigos válidos permanecem utilizáveis pelos mesmos leitores.

A estabilidade começa na **primeira ativação bem-sucedida**, como no catálogo
explícito existente. Um turno automático sem catálogo não cria snapshot vazio
nem estado negativo persistido. Se depois houver skills, a mesma sessão pode
capturar seu primeiro catálogo. Essa é uma decisão técnica proposta, distinta
de fixar um índice vazio desde a criação da conversa.

Depois de existir snapshot, seus bytes, ordem e hashes permanecem fixos nessa
sessão, inclusive após reinício, desligamento temporário, troca de modelo ou
edição da instalação. Não consultar filesystem para reindexá-lo. Skills novas
e versões novas só entram no catálogo de outra sessão. Cache corrompido não
autoriza recaptura silenciosa.

Desligar o índice não apaga corpos já lidos e registrados no histórico, nem
exclui snapshot ou assets persistidos. O aviso e o guia devem esclarecer esse
limite. Não misturar catálogo com `model_config`, parâmetros ou credenciais.

## Integração proposta

Preservar `InteractionEnvelope.skills_catalog: bool = False` para os
consumidores atuais: `True` continua sendo exigência explícita; `False`
continua desligado. Acrescentar `skills_catalog_auto: bool = False` como
intenção exclusiva da CLI. Validar booleanos estritos, recusar os dois campos
ativos simultaneamente e recusar qualquer um ativo em fonte não CLI. O
roteador de runtime também deve recusar a nova intenção se receber um
envelope indevido; a proteção não depende só do parser da CLI.

O parser traduz ausência/ligado/desligado para esses dois campos; funções
Python existentes podem conservar default desligado, aceitando explicitamente
o valor automático usado pelo parser. Não reinterpretar silenciosamente
`InteractionEnvelope(skills_catalog=False)` de integrações existentes.

Dentro de `_stream_owned`, resolver a intenção e derivar um envelope efetivo
com `skills_catalog_auto=False` e `skills_catalog` igual à disponibilidade
real. O restante do loop consome esse envelope e a mesma seleção já congelada.
Não repetir seleção, captura ou anúncio durante retries e rodadas de tools.

Captura e persistência continuam sob posse e pelo worker assíncrono atual.
Snapshot positivo usa o vencedor de `create_if_absent`; nenhum cache global
mutável. A capacidade fecha no `finally` atual. Cancelamento e perda de posse
continuam encerrando o acesso e aguardando operações próprias.

Preservar definição, dispatch e validações de `skill_view`, além dos vetos a
overrides. Nenhuma tool geral, MCP, escrita ou execução é autorizada por ter
índice. Web, gateways e outros emissores mantêm ambos os campos desligados,
sem descoberta adicional ou footprint no prompt.

## Avisos e contexto do modelo

Catálogo ativo mantém `skill_catalog_ready` e o aviso agregado existentes.
Automático indisponível produz uma decisão tipada com motivo `empty` ou
`model_tools_unavailable`; usar um evento distinto `skill_catalog_skipped`
para não anunciar um catálogo inexistente como pronto. Os campos desse evento
não contêm digest inventado, nomes, descrições ou paths. Validar o motivo.

Emitir no máximo um aviso de catálogo por turno, depois de `turn_start` e
antes do primeiro conteúdo do provider. A CLI consome ready/skipped em stderr
também no modo JSON, mantendo o NDJSON de stdout como hoje. O serializador
canônico reconhece o novo evento sem mudar os campos dos eventos antigos.
No modo `off`, não emitir aviso de catálogo.

Somente índice de nomes/descrições em JSON e preâmbulo estático entram no
system prompt. Corpos e referências continuam como resultados de leitura.
O índice não se duplica por rodada de tools ou retry. Persistência e limites
atuais — 256 skills, 32 KiB de índice e 128 KiB de páginas por turno — não são
ampliados. Documentar o custo padrão e o desligamento explícito.

## Aceite comportamental

- CLI real `run` e `chat`, sem flag, entrega índice e leitor ao adapter de
  teste com modelo apto e instalação real. Sem uma chamada de leitura, nenhum
  corpo/referência aparece na requisição do modelo.
- Interativo processa duas mensagens com o modo propagado, snapshot idêntico
  e capacidade nova em cada turno; desligado não oferece leitura.
- `on` conserva as recusas por modelo inapto e vazio; `off` não captura nem
  consulta catálogo persistido e recusa dispatch direto sem capacidade.
- `auto` com ferramentas `False` ou desconhecidas continua o chat sem I/O
  de catálogo; seleção realmente inválida continua falhando.
- Ausência final de `skills` e raiz válida vazia continuam sem catálogo;
  link, home inválido, permissão negada, I/O, só entradas inválidas e limites
  excedidos recusam sem chamar provider e sem vazar conteúdo.
- Primeiro turno vazio não persiste catálogo; instalação posterior permite
  primeira ativação. Snapshot positivo anterior, inclusive criado por cliente
  antigo, não muda após editar/instalar skills, reiniciar ou alternar flags.
- Corrupção, cache órfão, home divergente e asset adulterado continuam
  fail-closed, sem recaptura nem fallback para outra versão.
- Duas instâncias na mesma sessão respeitam posse e vencedor único. Sessões
  ou homes distintos não compartilham capacidade, páginas ou decisões.
- Cancelamento/perda de posse fecha a capacidade e não deixa trabalho órfão.
- Avisos ready/skipped têm motivo correto e vão apenas a stderr na CLI;
  não duplicam bloco system, não fabricam sucesso e não mudam NDJSON antigo.
- Web, gateways e Agent Runtime conservam requests e recusas existentes;
  auto ou on indevidamente injetados em envelope são recusados.
- Usar arquivos e SQLite reais e spies que executam o caminho original;
  não ler fonte Python em testes, congelar contagens do bundle ou depender de
  provider pago. Rodar integração, imagem e CI pertinentes antes de integrar.

## Riscos, decisões e limites

O padrão passa a divulgar nomes/descrições e consumir seu contexto na CLI
apta; o opt-out é explícito. Conteúdo instalado continua sendo dado não
confiável, sem autoridade de ferramenta. Escolher CLI primeiro limita a
abrangência, mas deixa outras superfícies para recorte posterior.

Não fixar estado vazio evita migração e preserva o formato antigo, com o
custo de permitir primeira ativação posterior na mesma conversa. Erros reais
continuam visíveis e podem exigir corrigir a instalação ou desligar o catálogo.
Essas decisões devem ser aceitas com o desenho, não inferidas da escolha CLI.

Rollback reverte comportamento/código. Nenhum formato novo persiste; clientes
anteriores continuam lendo seus snapshots válidos. O rollback não remove
conteúdo já registrado na conversa. Proteção contra o mesmo UID, telemetria,
curadoria, memória em bytes e ganho de latência não são novas garantias deste
recorte. Não repetir o benchmark do ledger para certificar esta interface.

O construtor revisou a proposta e a usuária aprovou este desenho escrito antes
do plano e da implementação. A proposta original não alterou código de produto;
as evidências da execução ficam no plano e no relatório de fechamento.
