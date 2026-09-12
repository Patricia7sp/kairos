# Configurações avançadas de provedores, modelos e Chat

Esta entrega completa os três itens identificados na revisão de 12/09/2026:
configurações avançadas de provedores, política de roteamento por perfil/conversa
e custo/política visíveis no Chat. A configuração operacional do Ollama permanece
adiada. Este documento não declara encerrado todo o escopo histórico do projeto.

## Provedores

Em **Provedores → Configurações avançadas**, o provedor **Personalizado** aceita:

- endereço base compatível com OpenAI Chat Completions;
- endereço do catálogo, na mesma origem do endereço base;
- autorização explícita de endpoint remoto;
- site e nome da aplicação, enviados como atribuição.

Não são aceitos segredos dentro de URLs nem headers de autenticação. As chaves
continuam no cofre. Trocar a origem do endpoint exige confirmar o envio da chave
armazenada ao novo destino. Depois de salvar, atualize o catálogo e teste a conexão.
Cada destino tem seu próprio cache; modelos descobertos no destino anterior não
são reutilizados no novo.

O **OpenRouter** permite configurar o site e o nome da aplicação. Essas opções
servem para atribuição, não para autenticação. Credencial configurada e conexão
testada são estados separados.

As opções são persistidas em `provider_settings` no `config.yaml` da instalação.
As APIs específicas fazem atualização atômica e preservam as demais configurações.
Uma requisição nova usa as configurações atuais; a resposta já em andamento mantém
o endereço, a credencial e o modelo com que começou.

## Modelos e política de roteamento

Em **Modelos → Escolher → Configurações avançadas**, modelos OpenRouter oferecem:

- permitir ou negar provedores que podem coletar dados;
- exigir suporte a todos os parâmetros enviados;
- permitir alternativas entre endpoints do mesmo modelo.

O padrão continua sendo coleta negada, parâmetros exigidos e alternativas
permitidas. O Kairos não troca o ID de modelo selecionado. Os campos correspondem
à [política oficial do OpenRouter](https://openrouter.ai/docs/guides/routing/provider-selection).

O diálogo permite aplicar a seleção à conversa/rascunho atual, definir o padrão
global, salvar um perfil e iniciar uma conversa com esse perfil. A tela identifica
o destino de cada ação. Salvar um perfil não altera o padrão global.

Parâmetros são combinados na ordem global, perfil, conversa e mensagem, com
prioridade crescente e combinação das opções internas de roteamento. A seleção
inicial do rascunho é registrada na conversa para sobreviver ao recarregamento.
Alterações pontuais de mensagem não substituem preferências já salvas da conversa.

Quando o cache de um modelo dinâmico expira, o próximo turno tenta atualizar o
catálogo do provedor selecionado. Se esse modelo não estiver disponível, a execução
falha sem recorrer a outro modelo.

## Custos

O Chat distingue preço de catálogo, custo da última resposta e política do próximo
turno. Cada resposta pode apresentar custo informado, estimado ou desconhecido.
As integrações atuais calculam estimativas pelo catálogo e pelo uso disponível;
uma estimativa não representa a fatura final do provedor.

O custo e o uso de novos turnos são gravados junto aos metadados da resposta,
inclusive para respostas parciais com erro. Valores zero são preservados. Respostas
antigas sem esses dados mostram custo desconhecido; não há reconstrução retroativa
com preços atuais.

## Chat e Agent Runtime

O Chat usa os provedores/modelos selecionados para mensagens e respostas. O
Agent Runtime gerencia sessões de trabalho do Codex em projetos autorizados,
incluindo ferramentas, aprovações, cancelamento e retomada. No backend Docker,
alterações são feitas em uma cópia isolada e passam pelo fluxo de revisão/entrega.
Sua autenticação é independente da chave do OpenRouter.

## Verificação

Os testes cobrem destinos e headers efetivamente usados, persistência de perfis,
política enviada ao modelo, atualização entre turnos, isolamento de turnos em
andamento, custos no histórico, rejeição de campos inválidos e fluxos reais do DOM.
O aceite no navegador usa uma instalação temporária com respostas de provedores
simuladas, sem credenciais de produção.
