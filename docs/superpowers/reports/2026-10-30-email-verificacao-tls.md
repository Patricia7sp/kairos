# Segurança e confiabilidade — verificação SMTP

Recorte sobre `main` em `52f51fe41081a580c20ad0207a6829bff4623d20`,
seguindo a continuidade de segurança/confiabilidade antes da curadoria.
Plano: [verificação SMTP](../plans/2026-10-30-email-verificacao-tls.md).

## Triagem dos alertas

A consulta pública do SonarCloud em 30/10/2026 retornou dois alertas abertos
no adapter de e-mail. A análise `f3645831-13c6-448c-9b3e-c36cfcee672e`, de
29/10, corresponde à base `52f51fe`. Isso descreve a main, não uma análise
destas alterações.

| Alerta | Evidência e tratamento |
| --- | --- |
| `AaE_nNq2A4HaqsrPZgDc`, S5332 | `verify()` abria SMTP e reportava sucesso sem negociar o STARTTLS configurado. Reproduzido contra servidor sem STARTTLS e certificados recusados. Agora compartilha a negociação do envio. `tls=False` explícito continua sendo uma escolha de configuração; não se declara eliminado todo transporte em claro. |
| `AaE_nNq2A4HaqsrPZgDd`, S4830 | O envio já usava `ssl.create_default_context()`. Os testes TLS reais confirmaram rejeição de CA não confiável e nome divergente nesse caminho anterior. Não foi reproduzida ausência de validação de certificado no envio; o alerta é candidato a falso positivo. Nenhum status remoto foi alterado. |

A investigação também reproduziu EHLO negativo tratado como verificação
positiva e ausência de QUIT após falha de negociação/autenticação. Os testes
observam o protocolo SMTP real, sem substituir `smtplib` ou `ssl`.

## Correção

Verificação e envio exigem EHLO aceito. Quando configurado, STARTTLS usa as
CAs padrão e valida o nome do servidor, seguido por novo EHLO. Falhas abortam
antes de AUTH/DATA. `verify()` continua sem autenticação ou envio; categorias
de `SendResult` permanecem iguais. O encerramento cobre negociação, login e
envio, e fecha o socket mesmo se QUIT falhar. Um envio aceito não é repetido
por falha no encerramento. Diagnósticos públicos contêm a classe do erro,
sem reproduzir respostas arbitrárias do servidor.

O contrato de EHLO/STARTTLS/QUIT foi conferido na
[documentação do Python 3.11](https://docs.python.org/3.11/library/smtplib.html).
Certificados e chaves dos testes são gerados em diretório temporário; apenas
a variável de confiança do ambiente é ajustada nos casos positivos e de
nome divergente. Não há credencial ou certificado de produção.

## Evidências

- Baseline: sete testes SMTP existentes aprovados.
- RED: quatro falhas de regressão no adapter; sete falhas/subfalhas nos
  cenários TLS, pelos comportamentos esperados. Logs locais:
  `/tmp/kairos-email-red.log` e `/tmp/kairos-email-tls-red.log`.
- GREEN: 74 testes e 327 subtestes aprovados nos módulos de SMTP/TLS,
  superfície CLI e entrega local do cron; `/tmp/kairos-email-green.log`.
- Complemento após revisão: EHLO inicial recusado no envio falhou contra o
  adapter real da base, extraído por `git archive` em `/tmp`, e passou com a
  correção. A rodada final SMTP/TLS aprovou 19 testes e oito subtestes;
  `/tmp/kairos-email-final-targeted.log`.
- Ruff completo: `check .` e `format --check .` aprovados.
- Revisão independente somente leitura por GPT-6 Luna/high: nenhum achado
  Critical/Important. A documentação do transporte é atualizada neste recorte.
- `scripts/ci.sh`: terminou com **exit 1 exclusivamente pela análise Sonar
  local interrompida**, conforme explicado abaixo; `/tmp/kairos-email-ci.log`.
  Todos os demais checks terminaram aprovados. Não se declara o script
  integralmente verde.

| Verificação local | Resultado |
| --- | --- |
| Suíte Python com cobertura | 4.237 testes e 6.560 subtestes aprovados; 21 skips e 34 deselects, sem `runtime_live`; 1.287,05 s |
| Clientes anteriores reais v8/v10 | Construção isolada, importação e dependências verificadas |
| Ruff, shellcheck, recall, lockfile | Aprovados; recall com 20 testes |
| TypeScript e Vitest | Três frontends aprovados; 17 testes TUI, 204 Web e 18 desktop |
| Imagens e receitas | Hadolint e builds/checks da aplicação, broker, stub e worker aprovados |
| Integração da imagem real | 33 testes e 26 subtestes aprovados, sem skips; 185,68 s |
| Sessões Docker offline | 26 testes aprovados, um skip do opt-in de AppArmor/seccomp no host; 55,20 s |
| SMTP/TLS instalado na imagem real | 19 testes unittest aprovados, incluindo o complemento pós-revisão; 11,363 s |

O smoke SMTP/TLS usou a imagem recém-construída, sem bind dos fontes de
produção: apenas os testes foram montados somente para leitura. Executou
com `--network none`, UID/GID 10000, `--cap-drop ALL`,
`no-new-privileges`, filesystem somente leitura e `/tmp` temporário.
O protocolo real usou somente o loopback do container. Log:
`/tmp/kairos-email-image-smoke.log`.

### Limite da análise Sonar local

Havia scanner e token disponíveis; o script iniciou o scanner sem parâmetro
explícito de branch/PR. A configuração e os argumentos dos processos foram
conferidos sem imprimir credenciais. A análise foi interrompida durante os
sensores, antes da geração/upload do relatório, para não atribuir a árvore
da feature à main. Não houve `report-task.txt` nem confirmação de análise.
O script continuou todos os demais checks e reportou a falha dessa etapa.
A análise do lote deve ser conferida no escopo do PR; não se presume aprovação
Sonar por sucesso de testes ou envio de análise. Não houve merge ou deploy.

A revisão observou a ausência de teste dedicado para EHLO inicial recusado
no envio. O complemento acima cobre essa lacuna, sem modificar o código de
produção. O CI integral já havia coletado os testes; a evidência de sua suíte
foi reutilizada para o mesmo código, com a execução adicional registrada
separadamente. A única edição posterior no adapter foi a descrição do módulo,
sem mudança executável. Não há achado de revisão pendente.

O teste local exige sockets de loopback. A execução inicial em sandbox
falhou com `PermissionError`; as execuções de baseline, RED e GREEN acima
usaram a permissão necessária e exercitaram o protocolo de verdade.
O CI usa Codex 0.154.0 extraído da imagem de testes existente; os três
frontends receberam cópias isoladas de dependências cujos lockfiles foram
comparados. Não houve chamada a provedor real nem alteração de produção.

## Publicação

O commit inicial é `ea9f349`. A revisão automática de permissões inicialmente
rejeitou `git push`, por não considerar comprovadas a autorização de envio e
a propriedade/confiança do remoto. A consulta autenticada ao GitHub confirmou
a mesma conta proprietária `Patricia7sp`, permissões admin/push e o repositório
público `Patricia7sp/kairos`, coincidente com o `origin`. Com essas evidências,
a revisão automática aprovou a nova tentativa; a branch foi publicada.
Nenhum merge ou deploy foi realizado. O fechamento será disponibilizado em
PR em rascunho, com CI remoto e Sonar próprios ainda a verificar.
