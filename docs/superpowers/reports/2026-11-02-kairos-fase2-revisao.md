# Revisão editorial histórica do plano mestre da Fase 2 (v0.1)

> Nota de atualização de 02/11/2026: relatório preservado como registro da revisão documental anterior, sem checkout/testes. O veredito abaixo refere-se à ordem antiga de I1 e está superado pela SPEC v0.2 e pelo plano revisado. Não atesta esta revisão nem conta como assurance independente do produto ou gate técnico de implementação. As afirmações originais permanecem abaixo para preservar autoria e contexto.

## Escopo e veredito documental

Revisei a SPEC original v0.1 (17 seções), a avaliação e o plano mestre E0–E6, incluindo a trilha S, as matrizes de requisitos e cenários globais. Nas rodadas de acompanhamento, reli as alterações de I1, autorização/alvo, eventos/evidências, exceções de auditoria e consentimento da voz contra os trechos aplicáveis da SPEC e verifiquei a coerência da ordem. Não consultei outro checkout nem rodei testes, scanners ou ferramentas de segurança. Não alterei Git nem os documentos revisados; este relatório é o único arquivo produzido.

**Veredito:** os quatro achados iniciais e a dependência de autorização levantada na segunda rodada estão encerrados no texto atual. A ordem de I1 — E0.1–E0.3 → gate E0 → E1.4 → E1.1 → E1.2 — é coerente com as dependências declaradas: primeiro valida o destino, depois entrega autoridade/alvo e aprovação autenticada, em seguida estabelece política de auditoria e por fim usa esses contratos na captura. Os pontos de parada impedem avançar sem o Mac ou sem autoridade autenticada, sem permitir atalho paralelo. Não encontrei contradição material nos trechos alterados nem outra lacuna material para a aprovação documental do plano.

Isso não autoriza a implementação: I1 continua descrito como escopo de uma autorização futura, e as seis decisões de produto continuam nos gates correspondentes. A conclusão tampouco aprova implementação ou atesta segurança do produto.

## Verificações da segunda rodada

- **I1 e dependências:** avaliação, linha 94, e plano, linhas 466–474, enumeram etapas, entregáveis e paradas. E1.4 antecede os consumidores: define `AuthorizedTarget` como projeto, identidade canônica/revisão do alvo e `ActionGrant` de leitura; valida antes da captura. E1.1 declara a dependência e E1.2 recebe o alvo autorizado. Isto corresponde à autorização por recurso/ação/parâmetros da SPEC, linhas 83–86, e ao E0→E1 do roadmap, linhas 254–256.
- **Exceções:** aprovação é uma ação própria pela superfície autenticada existente; `Principal` é derivado no backend e o `ActionGrant` é validado e revogável. Modelo não fornece a aprovação. Sem adaptador autenticado, exceções reais ficam desabilitadas e achados alto/crítico continuam `fail`; fixtures de política não abrem exceção real. E1.1 consome apenas registros validados. Isso atende à SPEC, linhas 141 e 145, sem antecipar implicitamente autorização.
- **Eventos:** contrato distingue versão de schema das revisões de política/configuração e enumera identidade, horário, correlação, justificativa e referências sanitizadas de chamadas de ferramenta; os testes de E1.6 cobrem unicidade, correlação e sanitização. Alinha-se à SPEC, linhas 87–90.
- **Voz:** E3.2–E3.3 cobrem ausência de captura, transmissão e TTS sem consentimento/configuração; escolha remota explícita e aviso; abertura/reconexão sem fala automática. Provider e modo local/nuvem seguem decisões de E3, como exige a SPEC, linhas 105–113.
- **Requisitos e gates gerais:** as matrizes ainda rastreiam UI-01–08, VOZ-01–07, AG-01–09 e os 16 cenários globais da seção 15. Permanecem diferidos, corretamente, provider de voz, alvos para testes ativos, visual, compartilhamento e orçamento de recorrência. A seção de estado do plano continua deixando claro que aprovar o documento não autoriza implementação, instalação, agenda, varredura ativa, publicação ou compartilhamento.

## Limites

Esta é uma revisão de rastreabilidade e clareza de planejamento, não uma revisão de código, pentest, validação de plataforma, confirmação das afirmações de capacidade do produto ou aprovação de segurança. Arquivos, interfaces e testes futuros são propostas; não validei essas capacidades no checkout. Os gates e testes descritos são planejados, não executados.
