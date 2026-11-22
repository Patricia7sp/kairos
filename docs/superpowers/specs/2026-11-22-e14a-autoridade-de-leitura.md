# E1.4a — quem pode ler qual revisão de um projeto

**Proposta para revisão independente.** O [aceite do E0](../reports/2026-11-02-fase2-plataforma.md#parecer-e0-para-o-primeiro-incremento--22112026) cobre o cliente web no Mac e o executor Linux. No primeiro incremento, uma auditoria só pode capturar uma revisão exportada pelo operador e autorizada para leitura. Autenticação de sessão, seleção de projeto e permissão de captura são três decisões distintas. Referências: [SPEC v0.2](2026-11-02-kairos-evolucao-v0.2.md) e [ameaças T01, T02, T04 e T07](2026-11-02-fase2-threat-model.md).

## De onde vem a autoridade

| Dado | Quem decide | O que o pedido pode informar |
| --- | --- | --- |
| `Principal` | O adaptador do backend após autenticar o operador. A [Web](../../../kairos_web/server.py) tem sessão de operador único; não identifica usuários individuais. CLI local depende de identidade de SO atestada; no runtime, UID de peer atesta o processo, não o operador em nome de quem ele age. Sem vínculo confiável, essa superfície não despacha a captura. | Nenhum campo de identidade ou permissão. |
| `AuthorizedTarget` | Registro de exportação confiável no executor: ID do projeto, revisão imutável, identidade da raiz e fingerprint esperado. O [catálogo de projetos](../../../kairos_runtime/projects.py) já publica revisão e fingerprint; um nome ou SHA sozinho não identifica um projeto autorizado. | Seletores de projeto e revisão, nunca um caminho livre, raiz alternativa ou manifesto enviado pelo cliente. |
| `ActionGrant` | Política local do operador, consultada pelo backend; autorização vinculada a principal, projeto, revisão, ação `read_snapshot`, versão da política, validade e estado de revogação. O cadastro de um projeto não gera esse grant. | Referência opaca, se necessária. Conhecer a referência não concede nem renova o direito. |

## Da seleção aos bytes

1. No despacho, autenticar e consultar **somente metadados confiáveis do backend**. Comparar principal, projeto, revisão, ação, prazo, revogação e versão da política. Sem registro, com política indisponível ou com dados conflitantes, recusar antes de listar o alvo, abrir arquivos de conteúdo ou iniciar o auditor. Não usar `discover_projects`/`project_metadata` como pré-checagem de permissão: a verificação do workspace pode ler conteúdo.
2. Na admissão da captura, repetir a consulta ao estado canônico. Serializar essa admissão com a revogação: se a revogação já foi confirmada, nenhuma nova captura começa. O leitor recebe a raiz atestada por descritor e limites do servidor. A primeira abertura e a travessia devem preservar a identidade autorizada: conferir um path e reabri-lo depois por nome não satisfaz o contrato. As [primitivas de árvore](../../../kairos_filesystem/tree.py) são o ponto de integração; se for necessário adaptar a entrada por descritor, fazê-lo antes de conectar o leitor.
3. Manter a captura privada até verificar identidade, integridade da árvore e fingerprint esperado. Antes de entregar os bytes ao consumidor, revalidar o grant; se houver revogação, vencimento ou troca durante a leitura, descartar a captura. Uma revogação posterior não recupera bytes já entregues. Só a captura validada pode alimentar E1.1/E1.2, sem reabrir a origem por caminho.

O [comando CLI atual](../../../kairos_cli/handlers.py) chama um [auditor](../../../kairos_security/auditor.py) que aceita paths e pode procurar fontes a partir do diretório atual. É uma interface local legada, não um adaptador autenticado de captura. O despacho novo não pode chamar esse caminho como fallback. Esta entrega não cria ferramenta de modelo nem expõe a interface legada pela Web.

## Como decidir se a implementação passou

Usar um projeto exportado de fixture, dois projetos com a mesma revisão e dois sujeitos de teste distintos no backend. Exercitar o despacho e o leitor reais. Observar a entrada na primeira operação de leitura de conteúdo e o recebimento pelo consumidor; uma resposta de erro sozinha não prova que nada foi lido.

| Cenário | Resultado exigido |
| --- | --- |
| Operador autenticado, grant atual e revisão exata | Captura chega ao consumidor com os bytes e fingerprint da fixture. |
| Sem identidade atestada, identidade declarada no JSON, grant de outro sujeito, projeto ou ação | Recusa antes de ler conteúdo, inclusive se os dois projetos tiverem o mesmo SHA. |
| Grant vencido, revogado, ou política alterada entre despacho e admissão | Nenhuma leitura; testar a ordem com uma barreira determinística. |
| Raiz substituída entre seleção e abertura | Recusar antes de ler o substituto. Divergência de conteúdo durante a captura ou revogação antes da entrega: nenhum byte entregue ao auditor. |
| Catálogo ausente, projeto legado sem identidade ou revisão indisponível | Recusar sem recorrer ao diretório atual, à instalação do Kairos ou a outro projeto. |

**Questões para o revisor:** a origem de cada vínculo permite aplicar essas recusas? Qual operação serializa revogação e admissão/entrega? O leitor consegue demonstrar que leu a mesma raiz que autorizou? A implementação só começa após resolver essas questões na revisão independente. Exceções de achados e aprovações genéricas pertencem a E1.4b.
