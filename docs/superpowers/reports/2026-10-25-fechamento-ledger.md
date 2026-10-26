# Fechamento do reuso do ledger — integração e deploy

O [PR #117](https://github.com/Patricia7sp/kairos/pull/117) foi marcado como
pronto para revisão e integrado por squash em
[`89020109ad1d063f06dba9b685681e15cb22590a`](https://github.com/Patricia7sp/kairos/commit/89020109ad1d063f06dba9b685681e15cb22590a),
em 25/10/2026, após autorização explícita de merge e deploy.

## Evidências da entrega

| Verificação | Resultado observado |
| --- | --- |
| Pai do squash | `b7346adeee312368746fddb3ca7cfe3058c49b99`, a base validada |
| Árvore integrada | idêntica ao head revisado `5c55de2`; diff pós-merge conferido, sem remoções acidentais |
| CI da main | [#313, run 40153981766](https://github.com/Patricia7sp/kairos/actions/runs/40153981766), completo e verde |
| Suíte remota da main | 4.102 testes e 6.542 subtestes; 24 skips e 33 deseleções; 764,24 s |
| Sonar da main | Quality Gate OK para `8902010`; cobertura nova 95,8%, duplicação 0%, ratings novos A |
| Deploy Komodo | job `121053820238`, sucesso; Update `6add6a33d2fd3e9eed2cbe30` |
| Checkout implantado | HEAD `89020109ad1d063f06dba9b685681e15cb22590a`, sem alterações rastreadas |
| Containers | aplicação e broker recriados e `healthy` |
| Smoke direto pós-deploy | `/api/health` respondeu `ok`, script com exit 0 |
| Fontes instaladas | hashes dos dois arquivos alterados e do serviço iguais ao Git integrado |

A análise Sonar da main tem período de código novo próprio. Seus 95,8% não
substituem nem contradizem os 96,3% do recorte do PR. A API confirmou a revisão
completa e o Quality Gate; sucesso de upload do scanner não foi usado como
aprovação presumida.

O wrapper do deploy registrou `commit '?'`, pois o campo esperado não veio
no Update. A revisão foi conferida diretamente no checkout usado pelo Komodo,
e a aplicação foi verificada por recriação, imagem, fontes e smoke. A prova
não depende desse campo ausente. Dados observados em
[proof.json](2026-10-25-fechamento-ledger/proof.json), com
[manifesto SHA-256](2026-10-25-fechamento-ledger/sha256.json).

## Resultado da #109 e preservação histórica

A [issue #109](https://github.com/Patricia7sp/kairos/issues/109) foi atendida
no recorte escolhido: baseline v9/v10/v11 reproduzível, observação estável
delimitada, reuso de registros positivos nas transações próprias de
`prepare`/`finish`, testes reais de invalidação e recusa e comparação pareada.

O [gate aceito](2026-10-25-reuso-ledger-correcao.md) teve ganho mediano de
11,507711479836553%, melhora nos três pares de 120, 903 → 775 reconstruções
e 20 validações preservadas em cada perfil. Nenhuma varredura de timeline,
schema ou política foi dispensada; não se afirma remoção de todo custo
proporcional ao histórico.

As rodadas anteriores continuam com seus resultados originais e seus dados
completos. Não há comprovação da causa histórica do par local que regrediu,
nem promessa de ganho universal, SLA, significância estatística ou memória
em bytes. Providers reais e caminhos pulados não recebem atestado.

O [PR #116](https://github.com/Patricia7sp/kairos/pull/116) foi substituído
pelo #117. Na árvore integrada, nove de seus dez arquivos são idênticos;
o harness evoluiu de forma explícita para perfis/hashes adicionais e inspeção
de integridade em conexão separada. A baseline JSON e seu relatório permanecem
inalterados. Não é necessário integrar a branch antiga sobre o runtime novo.

Rollback continua sendo reverter código e deixar o pipeline validar e
reimplantar. Não há migração ou cache persistido novo a restaurar.

## Continuidade

T1/T2/T3 do plano foram concluídas no escopo aprovado. O fechamento documental
não amplia o memo a outros donos ou elimina revalidações nas fronteiras.
O índice automático foi entregue posteriormente pelo PR #119, com
[integração e deploy verificados](2026-10-25-indice-automatico-cli.md).
`/learn`, curadoria e workflows duráveis continuam sendo recortes funcionais
próprios, sem aprovação de implementação derivada desta medição. Ollama
operacional permanece adiado.
