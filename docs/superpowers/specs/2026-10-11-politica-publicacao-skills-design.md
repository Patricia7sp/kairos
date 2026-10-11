# Política organizacional e coordenação de publicação — fatia B

Status: proposta futura separada; não aprovada nem implementada. Depende dos
contratos de resolução da [fatia A](2026-10-10-proveniencia-skills-design.md).
Não entra no plano A1/A2. B1 e B2 terão planos e gates próprios.

## Herdado e não herdado

`_reversa_sdd/skills/requirements.md:54` e `skills/design.md:93` exigem
`_org_mirror_write_guard` em delete/remove_file. Não especificam enrollment,
emissor, formato de política, identidade administrativa, override ou proteção
de toda edição. A fonte da instalação não autentica uma organização.

**D-CLI.SKILL.ORG**, em [docs/decisoes.md](../../decisoes.md), propõe uma
política local e ampliação conservadora a substituições destrutivas. A reserva
de `skills/_org/` é decisão nova do Kairos, não formato herdado obrigatório.
**D-CLI.SKILL.PROVENANCE** sustenta a separação entre publicação e autoridade.
Nada disso muda as fontes Reversa nem declara endurecimento já existente.

## B1: garantia limitada e política

A garantia é **continuidade da política configurada entre operações
cooperativas do Kairos**: não usar conteúdo, frontmatter, flag ou ausência
acidental da fonte para remover um veto registrado ou contornar a política
atestada na operação corrente. Não há garantia de
resistência ao usuário de execução. Um processo com o mesmo UID e acesso
a código/configuração/banco pode contornar essas verificações; situar um
arquivo fora do home não cria uma identidade privilegiada ou imutável.

O operador configura na composição do serviço uma referência de política
versionada e bindings por home canônico, nome e organization_id. A API não
recebe override de ferramenta nem seleção de emissor pelo modelo. Capturar
referência e identidade da configuração/fonte, ler por descritores e
revalidar antes de efeitos. Configuração selecionada não pode silenciosamente
virar modo sem política. Fonte configurada ausente/incompatível recusa.
Política válida que veda alvo recusa autorização; erro de leitura não significa
ausência de organização. IDs são rótulos administrativos, não credenciais.

Configuração explicitamente sem política permite apenas a proteção de
namespace/evidências disponíveis, com limite de cobertura declarado.
Home ou argumento CLI não redefinem a política do serviço durante uma
operação. Essa é uma disciplina da composição cooperativa, não uma atestação
criptográfica da configuração ou prova de inacessibilidade ao mesmo UID.
Isolamento por UID/mount/read-only, se desejado, exige outro recorte e testes
de implantação; não é requisito ou garantia implícita desta spec.

Reservar `_org` conservadoramente; organização ativa/inativa ou falta de
sidecar não libera o namespace. Não classificar cópias por conteúdo e não
implementar consumo/pull. Validar versão, bindings únicos, nomes e tipos;
não vazar fonte privada no diagnóstico. Binding por home impede colisão
com skill pessoal de outro home.

Proteção já registrada na instalação continua mesmo se uma entrada sumir
da política. Reader não registra observação, e recusa não cria instalação.
Um binding apenas visto por identify ou numa recusa não ganha persistência
histórica; após sua retirada válida da política, não há veto durável novo.
Essa limitação impede prometer continuidade para observações sem registro.
Revogação administrativa de veto fica para outro contrato: sem force ou
unprotect. Adoção/revogação de autoridade USER nunca revoga veto organizacional.

Guards nos serviços cobrem delete/remove_file herdados e, como ampliação nova,
substituição de destino materializado por sync/promoção. Não proibir por
inferência todas as edições in-place; edit/patch/write_file permanecem fora
da entrega. Reavaliar proteção em rollback/restore e recuperação.

Gate B1: fontes ausentes/corruptas/trocadas, mudança durante preparação,
bindings por home, namespace ativo/inativo, chamadas diretas e recuperação.
Explicitar nos resultados que a garantia não resiste a adulteração pelo UID.

## B2: escritores e publicação

Sync legítimo hoje pode trocar inode sem renovar prova; promoção pública
pode substituir destino sem journal comum. B2 coordena esses escritores
existentes, sem entregar download/busca/update do hub.

Cada publicação recebe ID, identidade observada, inventário e snapshot;
publicação não é automaticamente autoria USER nem concessão independente.
Composição do sync atesta a raiz confiável do pacote para BUNDLED; raiz
arbitrária não adquire essa autoridade por chamar a mesma função. Promoção
exige chamador/contexto definido e recibo sanitizado; source/trust declarado
não comprova proprietário. API sem contexto necessário recusa explicitamente.
Destino ocupado na promoção nunca é apagado diretamente.

Lock, journal e pendências são comuns. Publicação só renova provas que seu
contexto realmente sustenta; não transporta adoção antiga através de uma
substituição de conteúdo como se fosse restore. Uma publicação que conservar
autoridade derivada precisa de vínculo explícito autorizado, não mero nome.
Mudança administrativa de metadata não cria identidade instalada nova.

Registrar provas duráveis antes do movimento e revalidar política corrente
na recuperação. Estados com identidades esperadas, não somente bytes:

| Publicação | Estado observado | Recuperação |
|---|---|---|
| Inicial | Destino ausente e staging esperado | Abortar; preservar ausência e limpar só staging comprovado |
| Inicial | Destino novo esperado e staging ausente | Concluir se autorização/política ainda válidas |
| Substituição | Original no destino e staging novo esperados | Abortar preservando original |
| Substituição | Destino ausente; original privado e staging esperados | Repor original e abortar se guards permitirem; senão conflito |
| Substituição | Destino novo; original privado esperado; staging ausente | Concluir se autorizado, conservando snapshot anterior |
| Ambas | Identidade/prova divergente, duplicada ou incompleta | Preservar versões, registrar conflito e bloquear escritores |

Veto novo depois de publicar e antes de commit impede conclusão autorizadora;
não retirar automaticamente árvore protegida. Fonte indisponível mantém
pendência. Órfãos desconhecidos não são adotados nem apagados. Recuperação
não transforma publicação em concessão para solucionar conflito.

Migração aditiva de registros de publicação, timeline global, capability e
backup/reparo entram no plano B2. Registros A/antigos permanecem imutáveis.
Exclusões locais de sync e snapshots de turnos iniciados continuam preservados.

Gate B2: atualização legítima com nova identidade/prova; raíz arbitrária;
promoção sem sobrescrita; root trocada byte-idêntica; política durante staging;
SIGKILL e fsync nas fronteiras da tabela, inclusive substituição real na imagem;
backup/restauração verificados. Suíte local e CI antes de merge.

## Limite e aprovação

Não autenticar organização/distribuidor, oferecer enrollment ou override,
instalar política em produção ou implementar isolamento de runtime nesta
fatia. B é proposta independente: aprovação de A não aprova B1/B2 nem os
planos de publicação. Antes de execução, revisar a spec B e o plano da fase
correspondente, já com dependências e propriedade de arquivos por agente.
