# Fechamento da publicação B2 e do backup síncrono

Estado confirmado em 24/10/2026. Este registro consolida entregas observadas;
não representa nova execução dos testes históricos nem acesso adicional a dados.

## B2 — PR #114

O [PR #114](https://github.com/Patricia7sp/kairos/pull/114) foi integrado por
squash em `da7c58c00d3b9e9c7635cb173353ecb2144d586f`, após revisão de
`0aa269470f289fe14738692109f573fd8c1417b7`. A árvore integrada conferiu com a
revisão validada e o diff contra o pai foi inspecionado.

- Suíte local normal pós-revisão: 4.064 testes e 6.502 subtestes aprovados.
- [CI da main 305](https://github.com/Patricia7sp/kairos/actions/runs/40034445208):
  onze jobs aprovados, incluindo deploy; unitários com 4.061 testes e
  6.502 subtestes aprovados; imagem com 32 testes, sem skips.
- Komodo Update `6adbe424d2df05eb3b6f8cd6` concluído com sucesso.
  Checkout da stack conferido em `da7c58c`; 333 fontes Python verificadas
  por SHA-256 na aplicação e no broker. Ambos healthy, runtime ready.
- Backup privado anterior à migração, com restauração de 5.618 entradas
  comparadas. Ensaio v10→v11 preservou 30 tabelas canônicas e foi idempotente.
  Banco produtivo íntegro no schema 11, nove arquivos protegidos inalterados.
  Vinte e nove tabelas anteriores mantiveram seus hashes; `runtime_sessions`
  conservou 14 IDs/linhas e mudou somente `updated_at` em uma linha.

Os onze jobs aprovados não significaram Quality Gate da main aprovado: a
análise após #114 identificou o achado S7493 no backup. Essa pendência foi
tratada separadamente em #115. O [relatório local B2](2026-10-22-publicacao-skills-b2.md)
mantém os comandos, revisões e limites anteriores ao merge.

## Backup síncrono — PR #115

O [PR #115](https://github.com/Patricia7sp/kairos/pull/115) foi integrado e
implantado por squash em `b7346adeee312368746fddb3ca7cfe3058c49b99`, com árvore
idêntica a `f9d881ef9a430da73bb77098842fe1e4068321d3`. O diff pós-merge
continha somente os dez arquivos esperados. A API e o handler são síncronos;
o corpo e a ordem do I/O não foram alterados.

- RED→GREEN da chamada direta; 108 testes e 341 subtestes afetados aprovados.
- Suíte local normal: 4.064 testes e 6.502 subtestes, 21 skips e 33 deselections,
  em 749,40 s; arquivos rastreados estáveis durante a execução.
- A execução local com cobertura falhou em um caso de cancelamento do runtime
  sem alterações nesta branch. O módulo passou no modo normal; a divergência
  sob cobertura não teve causa conclusiva e permanece registrada no PR.
  Não se atribui sucesso ao `scripts/ci.sh` instrumentado dessa rodada.
- [CI da main 308](https://github.com/Patricia7sp/kairos/actions/runs/40046906636):
  onze jobs aprovados, incluindo deploy. Unitários: 4.061 testes e
  6.502 subtestes, 24 skips e 33 deselections, em 774,34 s. Imagem: 32 testes
  e 26 subtestes, sem skips, em 121,82 s.
- Komodo Update `6adc03dfd2df05eb3b6f8ef4` concluído com sucesso. O log
  não informou commit; o checkout foi conferido diretamente em `b7346ad`,
  com 333 fontes Python idênticas em cada container.
- Aplicação e broker healthy, readiness e smoke HTTP aprovados. A chamada
  direta e o executável instalado produziram tar/JSON válidos sobre home
  sintético temporário. Banco produtivo consultado somente em leitura:
  integrity OK, schema 11.
- [Sonar da main](https://sonarcloud.io/dashboard?id=Patricia7sp_kairos):
  análise `1a2f5add-86d8-46f3-9f39-bc886910151b`, revisão `b7346ad`,
  Quality Gate OK, confiabilidade nova A, cobertura nova 95,8%, sem condições
  ignoradas. O PR tinha cobertura nova de 100%; são escopos diferentes.

O backup adicional de produção proposto antes de #115 foi recusado pela
revisão automática de permissões e não foi executado. O pipeline autorizado
foi seguido sem nova cópia de dados; o backup privado de #114 foi reconferido
por checksum, com diretório 0700 e tar 0600. Rollback de código segue
[revert e pipeline](../../ci-cd-komodo.md#falhas-e-rollback).

## Continuidade e limites

A [issue #109](https://github.com/Patricia7sp/kairos/issues/109) é o próximo
recorte registrado: reproduzir o custo do ledger e fundamentar uma proposta
de otimização. Não há otimização de runtime nesta atualização documental.

Runtime live não foi acionado. O caso de integração Docker que exige perfil
AppArmor/seccomp indisponível no host permaneceu pulado. Os limites de B2
sobre fonte externa, pendência conservadora de limpeza, política organizacional
e adversário com o mesmo UID permanecem. O erro de fsync após replace no
backup continua sem promessa de rollback, conforme o
[desenho síncrono](../specs/2026-10-23-backup-sincrono-design.md).
