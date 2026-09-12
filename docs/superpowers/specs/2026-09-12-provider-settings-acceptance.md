# Aceite — configurações avançadas, política e custo do Chat

Data: 12/09/2026. Base: `d70a30b` (PR #22).

## Escopo

Três pendências autorizadas após a revisão da especificação original:

1. Configuração de endpoints personalizados e atribuição do OpenRouter na Web.
2. Política OpenRouter por perfil e conversa, com persistência e aplicação no próximo turno.
3. Exibição e persistência do custo de cada resposta, além da política ativa no contexto do Chat.

Ollama foi explicitamente adiado. Não houve alteração de credenciais ou configuração
da instalação de produção durante este aceite.

## Evidência operacional

O navegador usou a aplicação FastAPI e os arquivos reais desta entrega. Cofre,
banco e configurações foram criados em diretório temporário. As requisições de
provedores usaram `httpx.MockTransport`, com captura do modelo e da política enviados.

Em 390, 900 e 1400 pixels foram confirmados:

- persistência do endereço personalizado pela tela Provedores;
- perfil salvo sem alteração do padrão global;
- criação de conversa com o perfil e envio do ID de modelo selecionado;
- política enviada igual à selecionada na interface;
- custo estimado de US$ 0,00004 e perfil preservados após recarregar o histórico;
- ausência de erros JavaScript, transbordamento da página e corte do botão Enviar.

Relatório local: `/tmp/kairos-advanced-browser.json`.
Capturas: `/tmp/kairos-advanced-{providers,profile,chat}-{390,900,1400}.png`.
O servidor temporário foi encerrado após os testes.

## Verificações de integração

Testes específicos verificam atualização de configurações entre turnos, isolamento
de endereço/credencial/modelo de turnos em andamento, fechamento dos clientes HTTP
após cancelamento e atualização do catálogo expirado sem substituição do modelo.

A revisão identificou e corrigiu a combinação incompleta de políticas na interface,
metadados inválidos que poderiam impedir a leitura do histórico e cortes de layout
no celular. Atualizações parciais de parâmetros preservam as opções já salvas.

O pacote Python inclui os novos módulos e o arquivo JavaScript compartilhado de
combinação de parâmetros. A CI local usa o Codex 0.153.4 exigido pelo projeto.

Resultado final das suítes: 1.788 testes Python e 5.567 subtestes; 115 testes Web,
17 TUI e 18 desktop. Verificações de tipos e Ruff passaram. Um ajuste de formatação
apontado pela última execução foi corrigido e revalidado com `ruff format --check`.

## Limites deste aceite

O navegador valida a integração da aplicação com transporte simulado. Não certifica
disponibilidade, cobrança ou autenticação de provedores externos. Custos antigos sem
metadados continuam desconhecidos, e estimativas de catálogo não equivalem à fatura.

Merge, CI remota e implantação devem ser registrados separadamente da validação local.
