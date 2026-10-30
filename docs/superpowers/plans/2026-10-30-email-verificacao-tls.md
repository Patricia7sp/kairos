# Verificação SMTP e fechamento de conexões — plano de correção

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans para executar sequencialmente; revisão independente ao final.

**Goal:** Fazer a verificação SMTP respeitar STARTTLS e recusar negociação inválida, fechando conexões também nas falhas anteriores ao envio.

**Architecture:** Compartilhar a negociação SMTP entre `verify()` e `send()`. Aplicar o contexto TLS padrão com validação de certificado e nome; encerrar a conexão em um `finally` que abrange negociação, autenticação e envio.

**Tech Stack:** Python 3.11, smtplib/ssl, unittest e servidor SMTP de loopback existente.

**Spec:** `docs/decisoes.md`, D-T28.1, D-T28.2 e D-T28.5; contrato existente `tls`/STARTTLS do adapter. Continuidade de segurança/confiabilidade registrada em `docs/functional-progress.md`.

## Restrições

- Sem dependências novas ou mensagens externas.
- `verify()` não autentica nem envia; `tls=False` explícito conserva o transporte configurado.
- Preservar as categorias de `SendResult` e não repetir um envio aceito por falha no QUIT.
- S4830 é hipótese do scanner: o envio atual já usa `ssl.create_default_context()`; comprovar a validação com TLS real, sem desativá-la.

## Review Focus

- TLS anunciado mas recusado: nenhum AUTH ou DATA em claro.
- Certificado não confiável e nome divergente: recusa real da negociação.
- EHLO recusado antes/depois de STARTTLS: nunca reportar verificação positiva.
- Falha de autenticação e QUIT recusado: socket fechado e resultado primário preservado.
- Diagnósticos de negociação: não reproduzir texto arbitrário recebido do servidor.

### Task 1: Regressões e correção do adapter

Arquivos: `kairos_gateway/adapters/email_adapter.py`, `tests/smtp_sink.py`,
`tests/test_email_adapter.py`, `tests/test_email_tls.py`.

- [x] Baseline: sete testes existentes passam com SMTP real.
- [x] Reproduzir em RED a verificação positiva sem TLS/EHLO e o fechamento ausente após falhas.
- [x] Acrescentar certificados temporários e testes TLS reais: confiança, nome, sucesso e recusa de STARTTLS.
- [x] Compartilhar negociação com EHLO validado e contexto TLS padrão; garantir fechamento.
- [x] Executar os testes SMTP, CLI e entrega do cron afetados.

### Task 2: Validação integrada e revisão

- [ ] Executar `scripts/ci.sh` com ferramentas disponíveis; registrar skips e bloqueios explicitamente.
- [x] Solicitar revisão independente do diff e resolver achados importantes com RED→GREEN (nenhum Critical/Important encontrado).
- [ ] Registrar resultados, escopo do Sonar e pendências de integração no checkpoint.
