# Backup síncrono no CLI

Abordagem escolhida pela usuária e execução autorizada por “continue”, em
23/10/2026. Base: main `da7c58c` (B2 integrada e implantada).

O único bug novo de confiabilidade na análise da main é `python:S7493`, no
`os.fdopen` de `kairos_cli/backup.py`. `run_backup` é declarado async, não
usa await e seu único chamador de produção é o comando CLI isolado.

## Contrato e abordagem

Tornar `run_backup(home: Path, args) -> int` síncrono e chamá-lo diretamente
de `cmd_backup`. Chamadores de teste e o harness da imagem também passam a
chamar diretamente. CLI, JSON, códigos de saída, arquivo produzido, lock,
fsync, descarte de parcial e recusa com saída anterior preservada mantêm
seus contratos. A ordem e o corpo das operações de backup não mudam.

A preservação da saída anterior cobre recusas antes da publicação. Já existe
um limite após `os.replace`: se o fsync do diretório falhar, a função retorna1
sem atestar durabilidade, mas o novo arquivo pode estar visível no destino.
Retorno de erro não promete rollback depois desse ponto. Essa semântica foi
reproduzida na revisão e permanece igual; recuperação pós-publicação é outro
recorte. O CLI atual cria um destino novo; output é um argumento do helper,
sem opção correspondente exposta pelo parser.

Manter async exigiria offload integral e um contrato adicional de cancelamento
e cleanup; não corresponde ao consumidor atual. Suprimir S7493 não alinha a
assinatura ao trabalho efetivamente síncrono. A usuária escolheu o fluxo
síncrono, sem dependência nova, thread ou mudança do Quality Gate.

## Aceite

Teste real de backup por chamada direta RED→GREEN; regressões de fronteiras,
WAL, dados organizacionais, adoção e publicações; CLI real e aceite da imagem.
Suíte local completa, revisão independente e checks remotos. A análise do PR
não comprova o resultado da main antes de integração e nova análise.
