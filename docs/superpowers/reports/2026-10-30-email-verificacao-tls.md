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
- Ruff completo: `check .` e `format --check .` aprovados.
- Revisão independente somente leitura por GPT-6 Luna/high: nenhum achado
  Critical/Important. A documentação do transporte é atualizada neste recorte.
- CI integral: em execução; `/tmp/kairos-email-ci.log`. Ainda não há resultado
  atribuído a esta execução, nova análise Sonar, CI remoto, merge ou deploy.

A revisão observou a ausência de teste dedicado para EHLO inicial recusado
no envio; o caminho compartilhado é coberto pela verificação e por EHLO
pós-TLS nas duas operações. Essa ampliação de cobertura fica pendente.

O teste local exige sockets de loopback. A execução inicial em sandbox
falhou com `PermissionError`; as execuções de baseline, RED e GREEN acima
usaram a permissão necessária e exercitaram o protocolo de verdade.
O CI usa Codex 0.154.0 extraído da imagem de testes existente; os três
frontends receberam cópias isoladas de dependências cujos lockfiles foram
comparados. Não houve chamada a provedor real nem alteração de produção.
