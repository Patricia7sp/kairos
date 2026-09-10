# Aceite do repositório real do Kairos

Concluído em 2026-09-10, com evidência `status=accepted`. O runtime está
autorizado para `/projects/current` e `/projects/kairos`. O segundo projeto
usa uma exportação limpa dos arquivos versionados do Kairos, montada somente
leitura no broker; cada sessão continua trabalhando em sua própria cópia.

## Implementação e versão implantada

- [PR #17](https://github.com/Patricia7sp/kairos/pull/17): o healthcheck exige
  inspeção bem-sucedida da imagem configurada do worker. Imagem ausente,
  daemon inacessível e configuração inválida retornam falha sem imprimir
  dados. O Compose ganhou a montagem `/projects/kairos`, preservando o
  projeto sintético e exigindo autorização explícita na configuração.
- Commit do broker: `f409603c94c4409ca1b5906a286f2fef272abc67`.
- O primeiro aceite com retomada identificou uma falha adicional na interface:
  o Chromium rejeitava o código `1012` no fechamento WebSocket iniciado pelo
  cliente. O [PR #18](https://github.com/Patricia7sp/kairos/pull/18) mudou a
  reconexão para o código de aplicação `4000` e reforçou o teste de dois saltos
  na sequência, retomando somente pelo último cursor aplicado.
- Commit final da aplicação e do stack: `7e8f0102a3ba8af18165aa35c02c8c7e22ed3eb6`.

| Componente | ID conferido em produção |
| --- | --- |
| Aplicação | `sha256:23f0fbe3f2aa01747d2309e0178af313ec11714aceeed99fbbf1efed896ea38a` |
| Broker | `sha256:4504e63958cf09d8d1ed58f4b589aac9ca2ee15235f20e72c070c9d43c0a90ac` |
| Worker | `sha256:f482b46cf7edafc620e770d3d5cdd020a89e9e380b0cf39129522b2eefadcc53` |

## Projeto e isolamento

A montagem direta do workspace encontrou `.superpowers` privado para o UID do
broker. As permissões desse workspace foram preservadas. Foi usado `git archive`
do commit `e4097af3776c85c04b7b4cf4264790ac1f44444b`, cuja árvore coincide com a
do merge do PR #17, para exportar exatamente 1.538 arquivos versionados.
Metadados privados não versionados não entraram na exportação.

Origem no host:
`/home/paty7sp/.local/share/kairos-runtime-projects/kairos-e4097af3776c-source`.
O snapshot foi validado com a imagem do broker e UID `10000`: 2.047 entradas,
20.288.721 bytes de arquivos e archive de 21.739.520 bytes, dentro dos limites.

A exportação permanece fixada nesse commit. Ela não acompanha automaticamente
o workspace nem os commits posteriores da interface. A atualização do projeto
disponível para novas tarefas é uma operação explícita, descrita em
[Runtime em produção](../../runtime-production.md#autorizar-o-repositório-real-após-o-aceite-sintético).

## Testes e revisão

- Regressões observadas antes das correções, incluindo os três casos de
  configuração malformada apontados pela revisão independente.
- 28 testes focalizados de healthcheck/Compose aprovados; lint e formatação
  aprovados. A CI local completa anterior ao ajuste final de `ValueError`
  teve 1.579 testes e 5.562 subtests aprovados; os casos focalizados foram
  repetidos após o ajuste.
- Imagem final do broker testada contra Docker real com socket de status
  isolado: imagem existente retorna `0`, imagem ausente retorna `1`, ambas
  sem stdout/stderr e em aproximadamente 1,1 segundo.
- 59 testes Web e TypeScript aprovados após a correção WebSocket; a regressão
  falhava antes com o mesmo `InvalidAccessError` observado no Chromium.
- Revisões independentes concluídas sem pendências.
- Nove checks aprovados nos [PR #17](https://github.com/Patricia7sp/kairos/actions/runs/34494893233)
  e [PR #18](https://github.com/Patricia7sp/kairos/actions/runs/34528169420).
  CI dos merges [#17](https://github.com/Patricia7sp/kairos/actions/runs/34495385494)
  e [#18](https://github.com/Patricia7sp/kairos/actions/runs/34528819088) concluídas com sucesso.

## Aceite pelo Chromium

Ambas as sessões foram criadas e operadas pela Web em HTTP no Tailscale.
O teste também usou o WebSocket nativo para uma reconexão explícita, reabriu
a sessão pela lista de Sessões e recarregou a página antes do segundo turno.
Cada segundo turno leu dados do checkpoint anterior. Houve zero erros JavaScript
nos aceites finais, e as respostas do assistente foram conferidas no banco.

| Modo | Sessão | Primeiro turno | Turno de retomada |
| --- | --- | --- | --- |
| `read_only` | `a0d88abb938e4772b57c4c6241b7605b` | `22181c28ee504ab3b60cc599a5f80fa5` | `4588187b0925459a9fe52c40452c06ad` |
| `workspace_write` | `9ef43afcdee34a03af41092933272ae3` | `7518d77af1e9492eb86db2381d38c54a` | `8c8e05ca118d4badbf9d762f612e1011` |

Os quatro turnos terminaram `completed`. O checkpoint de leitura preservou
todos os 1.538 arquivos. O de escrita preservou esses mesmos arquivos e
acrescentou somente `docs/runtime-workspace-acceptance.md`, com o conteúdo:

```markdown
# Aceite de escrita isolada

Projeto: Kairos
Resultado: KAIROS_ESCRITA_ISOLADA_OK
```

O diff foi revisado: um arquivo acrescentado, nenhum modificado ou excluído.
A alteração de prova **não foi aplicada** ao repositório original nem à
exportação montada no broker. Hashes e modos do checkpoint foram comparados
com a origem; os hashes da exportação no host permaneceram idênticos.

## Backup e estado final

Backups privados restaurados em volumes separados antes das implantações:

- `20260910T152230Z`: 3.729 arquivos, cinco sessões, 16 mensagens, três turnos e
  três checkpoints; SHA-256 `fb365e30893df14d6885ac06e8163364be71d714822924af859bb34ff1bbb560`.
- `20260910T204422Z`: 4.238 arquivos, sete sessões, 24 mensagens, sete turnos e
  cinco checkpoints; SHA-256 `403c93a9bbeb7a770ecf610a1ff3dc09cd6a88946348255571a0c308e1ab1c48`.

Ambos estão sob `/home/paty7sp/.local/share/kairos-production-backups/`, com
configurações anteriores e evidências privadas. O segundo backup precede os
quatro turnos finais de aceite. Imagens anteriores preservadas pelas tags
`kairos:broker-rollback-real-project-20260910` e
`kairos:rollback-browser-reconnect-20260910`.

A auditoria final confirmou aplicação e broker saudáveis, banco íntegro,
zero turnos ativos, zero workers registrados e os marcadores `PRODUCAO_OK` e
`PERSISTENCIA_OK` do aceite sintético anterior preservados. O volume continua
`kairos_kairos-data`, a aplicação permanece sem socket Docker e a porta é
publicada somente em `100.87.25.101:9119`.

Evidências finais em
`/home/paty7sp/.local/share/kairos-production-acceptance/20260910-real-project/`:
`acceptance.json`, registros e capturas de tela dos dois modos, e
`workspace-change.diff` com a alteração isolada revisada.

Este aceite não cobre recuperação de turno interrompido, execução de tarefas
arbitrárias ou aplicação automática de mudanças ao repositório original.
