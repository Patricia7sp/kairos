# Reuso transacional do ledger — resultado não aceito

Este arquivo preserva a avaliação histórica da candidata `e86f49f`.
A [correção e nova avaliação solicitadas em 25/10](2026-10-25-reuso-ledger-correcao.md)
ficam separadas; não reclassificam os resultados abaixo.

**O ganho não foi consistente nos três pares. A candidata permanece experimental,
sem recomendação de merge ou deploy.** Na rodada válida, as reconstruções do
cenário 120 caíram de 903 para 775 e as 20 validações da timeline foram
preservadas. Os ganhos pareados foram −22,7371%, +26,7588% e +15,2136%:
a mediana de 15,2136% supera 10%, mas o primeiro par regrediu. O gate exige
melhora nos três, portanto `accepted=false` e exit 1 estão corretos.

O incremento implementa somente o [desenho aprovado](../specs/2026-10-24-reuso-ledger-por-checagem-design.md)
e o [plano](../plans/2026-10-24-reuso-ledger-por-checagem.md): memo privado
nos callbacks próprios de `prepare`/`finish`, com descarte antes de
commit/rollback/retry. Schema, dependências, serviços, guards e os corpos e
chamadas dos validadores permanecem preservados.

## Revisões e reprodução

| Papel | Revisão |
| --- | --- |
| Base de implementação | `28131179f776c0e18275fcb78e4c4a440d34d042` |
| Baseline B2/v11 | `b7346adeee312368746fddb3ca7cfe3058c49b99` |
| Candidata da rodada inicial | `3e46d0f3c9145b911a10a4b4f476a29e1199382e` |
| Candidata da rodada corrigida | `e86f49f66c166fb9089a390d6e2eb4b263fcd6ce` |

As seis execuções de cada rodada usam o mesmo interpretador, ambiente e
harness dentro daquela rodada. Ordem AB/BA/AB; schema 11; prefixes 0/40/120;
sete amostras por cenário; aquecimento e perfil separados dos tempos.
Cada execução cria até 129 árvores reais e verifica conteúdo, identidade,
eventos preparados/concluídos e integridade SQLite. Nenhum provider ou dado
de produção participa. Não houve testes, builds ou probes concorrentes
durante as rodadas. Leitura de logs e revisão estática ocorreram; os probes
diagnósticos foram executados somente depois dos tempos correspondentes.

```bash
env -u COVERAGE_PROCESS_START -u COVERAGE_FILE -u KAIROS_COVERAGE_ROOT \
  .venv/bin/python -m scripts.benchmark_skill_writer_pairs \
  --baseline b7346adeee312368746fddb3ca7cfe3058c49b99 \
  --candidate e86f49f66c166fb9089a390d6e2eb4b263fcd6ce \
  --output-dir .superpowers/sdd/2026-10-24-reuso-ledger-por-checagem/paired-isolated-integrity
```

O diretório deve ser novo: o driver recusa sobrescrever uma rodada.
Ele confere SHA por Git, hash do harness e dos módulos carregados, schema,
amostras, medianas recalculadas e evidências de fixture. Dados incompatíveis
ou subprocesso falho terminam com erro explícito. O gate não arredonda nem
seleciona somente a melhor execução.

## Rodada inicial e interferência do diagnóstico

Os [dados iniciais](2026-10-24-reuso-ledger/initial-protocol/summary.json)
permanecem completos, com resultado negativo original. Nos cenários 40/120,
o perfil mostrou 343/903 reconstruções reais, iguais à baseline. A queda do
wrapper `_get_validated` para 247/647 não era redução de reconstruções:
o fallback chamava diretamente o carregador sem memo.

A revisão independente encontrou um efeito colateral do próprio harness:
`verify_fixture` executava `PRAGMA integrity_check` na conexão reutilizada
pelos cenários seguintes. No SQLite deste ambiente, isso acrescenta `temp`
a `PRAGMA database_list`. O veto TEMP aprovado tornava os memos seguintes
inelegíveis. Probes com SQLite e a fixture/API/função reais confirmaram a
transição; isso não comprova que um add normal crie TEMP.

**A rodada inicial é inadequada para concluir sobre ROI em conexão elegível.**
Ela não foi reclassificada como aceita. A correção executa a mesma inspeção
numa conexão separada `mode=ro` do mesmo banco já comprometido. A conexão
medida conserva seu namespace; a inspeção continua detectando inconsistência
real de NOT NULL. Runtime, veto TEMP, consultas de domínio, amostras e
limiares não foram ampliados. A única nova rodada integral usou esse
harness corrigido; não houve terceira tentativa para procurar aprovação.

| Hash | Inicial | Corrigido |
| --- | --- | --- |
| Harness SHA-256 | `677c19891ab704bc0db81be618439808f3e2c8f8f908ac26c9174ff42614a9fd` | `ca919b8a0caa4e23d01cad223274177d7b7122856d6b62a1039848ff7bddeb83` |
| Driver SHA-256 | `0d1f55fa583dda210cf669e95ebaeb5b922194fab1e3ded605d22c0321f8b499` | igual |

A baseline histórica do PR #116 e seu JSON com `script_sha256=d93b41e…`
foram conservados sem alteração.

## Rodada corrigida

[Summary e referências aos seis brutos](2026-10-24-reuso-ledger/corrected-protocol/summary.json).
Ganhos abaixo são `100*(mediana_baseline-mediana_candidata)/mediana_baseline`;
valor negativo significa regressão. Os números da tabela são apresentação;
o gate usou as amostras completas sem arredondamento.

| Cenário | Par | Baseline (ms) | Candidata (ms) | Ganho |
| --- | --- | ---: | ---: | ---: |
| 0 | 1 | 87,407 | 90,649 | −3,7085% |
| 0 | 2 | 88,171 | 91,122 | −3,3473% |
| 0 | 3 | 101,901 | 74,861 | +26,5351% |
| 40 | 1 | 375,902 | 361,029 | +3,9567% |
| 40 | 2 | 449,444 | 354,995 | +21,0145% |
| 40 | 3 | 685,761 | 590,224 | +13,9315% |
| 120 | 1 | 1.097,767 | 1.347,368 | −22,7371% |
| 120 | 2 | 1.649,281 | 1.207,952 | +26,7588% |
| 120 | 3 | 1.520,777 | 1.289,413 | +15,2136% |

| Gate | Resultado |
| --- | --- |
| 20 validações em todos os perfis | preservadas |
| Reconstruções reais reduzidas nos três pares de 120 | 903 → 775 em todos |
| Mediana de ganho em 120 ≥10% | 15,2136%, atende |
| Melhora em todos os pares de 120 | **não atende: par 1** |
| Regressão mediana >5% e >5 ms em 0/40 | ausente |
| Resultado integral | **reprovado**, `scenario_120_did_not_improve_in_every_pair` |

A dispersão foi ampla. Em 120, as faixas baseline/candidata foram,
respectivamente: par 1, 970,909–1.698,956 / 875,657–2.113,179 ms; par 2,
1.330,557–1.865,737 / 876,949–1.714,216 ms; par 3,
1.285,508–1.898,581 / 859,845–1.533,903 ms. Os brutos incluem todas as sete
amostras e faixas dos demais cenários. Não houve incidente externo comprovado
que autorize descartar o par 1. O limiar e a exigência dos três pares permanecem.

Ambiente: Python 3.11.16, SQLite 3.53.1, Linux
7.0.0-38-generic x86_64/glibc 2.43, afinidade de quatro CPUs. A carga inicial
de um minuto variou de 0,513 a 1,358 entre execuções; os três valores de
`getloadavg` estão em cada bruto. A carga não foi mantida artificialmente
constante. Estes dados não estabelecem SLA ou significância estatística.

## Trabalho real, custo e limite

| Cenário | Wrapper baseline/candidata | Reconstruções baseline/candidata | Hits derivados |
| --- | ---: | ---: | ---: |
| 0 | 63 / 63 | 63 / 55 | 8 |
| 40 | 343 / 343 | 343 / 295 | 48 |
| 120 | 903 / 903 | 903 / 775 | 128 |

Essas relações se repetem nos três pares corrigidos. Os perfis registram
também `_get_validated_uncached`, `_base` e `_blob`; não são 903 reconstruções
na candidata, nem se pode chamar a contagem de entradas no wrapper de ganho.
A redução de reconstruções em 120 é 14,17497%; ganho temporal é outra medida.

O observador executou dez vezes por perfil corrigido. Seu tempo cumulativo
perfilado foi 0,626–1,911 ms em 0; 0,698–0,907 ms em 40; 0,727–0,868 ms em
120. São custos sob cProfile, separados das amostras de latência, e não
devem ser subtraídos delas. Não houve saturação: 128 registros anteriores
ficam abaixo do limite de 1.024. O teste com limite reduzido e três registros
reais comprova misses adicionais sem crescimento ilimitado ou mudança de
semântica. Consumo em bytes não foi medido.

## Verificação e revisão

- Vertical funcional T2: 503 testes e 358 subtestes aprovados, dois skips.
- Helper e integração do dono: 27 testes e cinco subtestes aprovados.
- Gate pareado e inspeção de fixture: nove testes e 32 subtestes aprovados.
- O teste de namespace foi observado falhar antes da correção e passar depois.
  A primeira fixture negativa com CHECK não demonstrou corrupção neste
  SQLite; foi substituída por inconsistência real de NOT NULL, detectada.
- Ruff e formatação dos arquivos alterados e `git diff --check` passaram.
- A primeira execução do CI foi interrompida deliberadamente após o achado do
  harness; não é evidência de aprovação. A execução integral final terminou
  com exit 1 apenas por formatação dos snippets Python do plano Markdown.
  Corrigido o documento, `ruff check .`, `ruff format --check .` (719
  arquivos) e `git diff --check` passaram. A suíte íntegra não foi repetida
  por essa edição documental; runtime, testes, dependências e configuração
  permanecem idênticos à candidata medida.

| Verificação integrada local | Resultado |
| --- | --- |
| Suíte principal com cobertura | 4.100 passed; 21 skipped; 33 deselected; 6.539 subtestes; 1.278,69 s |
| Recall G-19 | 20 passed |
| TUI / web / desktop | 17 / 204 / 18 testes; TypeScript dos três aprovado |
| Shellcheck e lock | aprovados |
| Hadolint e checks estruturais Docker | aprovados |
| Imagens principal, broker, stub e worker | construídas; probe do broker aprovado |
| Imagem real instalada | 32 passed e 26 subtestes; 209,01 s |
| Sessões Docker isoladas offline | 26 passed e um skipped; 68,08 s |
| Sonar local | skip explícito; scanner/token ausente, `SONAR_TOKEN` removido do ambiente |

A publicação da branch foi recusada pela revisão automática por falta de
autorização específica para expor esse conteúdo no repositório público. Não
há PR, checks remotos ou análise Sonar desta revisão. Sucesso de upload não
provaria Quality Gate. Os skips e as deseleções acima não são aprovações dos
caminhos não executados; providers reais/runtime_live não foram acionados.

A revisão independente conferiu hashes, medianas, manifestos e gates dos
dois conjuntos de brutos, identificou a interferência do harness e revisou
a correção com prova RED→GREEN. O resultado é **não pronto para merge**:
a reprovação de ROI persiste apesar dos testes funcionais aprovados, e os
checks remotos não foram executados. O parecer completo fica no ledger local.

Dois achados menores ficaram documentados para decisão posterior: erros
`io`/`conflict` do observador podem chegar como `corrupt` pelo wrapper
existente; o helper verifica tipo/home, mas não confere o ID do registro
retornado pelo loader antes de armazená-lo. Os call sites atuais fecham
corretamente sobre o ID; não foi demonstrado bypass de autoridade. Ambos
merecem tratamento antes de ampliar este helper a outros donos.

## Decisões registradas e limites

- Usar a base 2813117 conserva spec/benchmark aprovados e não faz merge do
  PR #116. Custo se incompatível com a base remota: rebase documental e nova
  conferência da revisão proposta, sem efeito em produção.
- Guardar os tempos no workspace deste plano evita ocupar o diretório de
  outro plano sugerido pelo exemplo. Custo: somente ajuste de referências
  locais de artefatos; protocolo e dados permanecem identificados.
- Isolar a inspeção `mode=ro` corrige a contaminação observada sem relaxar
  elegibilidade. Custo se houvesse escritor concorrente: inspeção poderia
  observar outro snapshot; as fixtures não têm escritor concorrente nessa
  fronteira e a prova negativa confirma leitura dos dados comprometidos.

Os guards existentes continuam responsáveis pela identidade física e pela
autoridade. O memo não autentica o inode usado por SQLite nem acrescenta
proteção contra o mesmo UID; controle transacional externo continua fora do
dono admitido. A recuperação de runtime, schema e política não foi ampliada.
Rollback é reverter o código; não há migração ou cache persistido a restaurar.

Limites explicitamente mantidos pelo Root após a revisão: CI/Sonar remotos
pendentes bloqueiam aceite; providers reais e caminhos pulados continuam
sem atestado; mesmo UID e controle transacional externo conservam os limites
do desenho aprovado; não se infere SLA, significância estatística ou memória
em bytes; continuidade física depende dos guards, nunca do memo. Os custos
residuais são, respectivamente, integração remota não verificada, cobertura
restrita aos caminhos executados, ameaças fora da garantia, conclusões
limitadas à rodada e ausência de autenticação física pelo próprio cache.

## Artefatos locais preparados para revisão

Cada rodada inclui JSON e log das seis execuções, summary e
[manifesto inicial](2026-10-24-reuso-ledger/initial-protocol/sha256.json) /
[manifesto corrigido](2026-10-24-reuso-ledger/corrected-protocol/sha256.json).
Somente o prefixo do diretório pessoal do interpretador foi normalizado para
`$HOME` nas cópias publicáveis; números, ordem, revisões e hashes de fontes
permanecem originais. Os manifestos correspondem às cópias preparadas. Os
brutos locais anteriores à normalização ficam no workspace da execução.
