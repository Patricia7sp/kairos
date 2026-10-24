# Issue #109 — baseline de add em A2, B1 e B2

Medição realizada em 24/10/2026, depois da implantação de B2 e do backup
síncrono. [Dados brutos](2026-10-24-skill-writer-benchmark.json), incluindo
amostras individuais, ambiente, revisões, hashes e contagens. Nenhum código
de runtime foi alterado, nem houve acesso a dados ou credenciais de produção.

## Método reproduzível

```bash
.venv/bin/python scripts/benchmark-skill-writers.py \
  --output /tmp/kairos-skill-writer-benchmark.json
```

O [script](../../../scripts/benchmark-skill-writers.py) arquiva somente os
pacotes de cada commit em diretórios temporários e executa cada revisão em
processo próprio. Confere que o serviço importado pertence àquela extração.
Não usa a biblioteca atual como substituto da revisão histórica.

| Revisão | Schema explicitamente migrado | Origem |
|---|---:|---|
| `d730822242f287a2a0268e9c5d66d4779c7477e0` | 9 | A2 integrada |
| `f9f9a6d5012a10dd96d3f1227df7b0689ddc841f` | 10 | B1 integrada |
| `b7346adeee312368746fddb3ca7cfe3058c49b99` | 11 | B2 com backup síncrono |

Todas as criações, inclusive o prefixo, usam `SkillMutationService.add` com
arquivos, SQLite, journal, fsync e diretórios reais. Não há identidades
sintéticas importadas de v7. A operação medida é a chamada do serviço com
conexão aberta; construção do serviço, migração, escrita do arquivo de entrada,
importações, CLI e verificações posteriores ficam fora do intervalo.
Em B1/B2 a sessão de política pessoal `none` é real e permanece aberta.

Cada cenário começa com 0, 40 ou 120 criações. Uma criação aquece o caminho,
sete outras são medidas sem profiler e uma execução adicional é perfilada
separadamente. Assim, as amostras têm respectivamente 1–7, 41–47 e 121–127
criações anteriores; o perfil tem 8, 48 ou 128. O cenário é incremental:
as criações anteriores continuam como parte do prefixo seguinte.

Cada add deve retornar COMMITTED, publicar o conteúdo correto e aparecer na
instalação corrente. Ao fechar cada cenário, a timeline ativa deve conter dois
eventos por criação, sem estado final pendente, e o banco deve estar íntegro.
Todas as árvores anteriores conservam device/inode e bytes. No cenário final
são 129 árvores reais e 258 eventos conferidos por revisão.

Ambiente comum: Python 3.11.16, SQLite 3.53.1, Linux x86_64/glibc 2.43,
afinidade de quatro CPUs. O JSON registra plataforma e carga inicial. As
dependências do ambiente local atual foram usadas também nas revisões antigas;
não é reprodução dos ambientes históricos de instalação.

SHA-256 do script executado, já formatado antes da medição:
`d93b41efd2af6bf305649be9a0e55190632f07621bfdcae80841656764bf170c`.

## Resultado

Medianas em milissegundos; todos os cenários concluíram as verificações:

| Criações antes do aquecimento | A2/v9 | B1/v10 | B2/v11 |
|---:|---:|---:|---:|
| 0 | 23,83 | 49,86 | 122,82 |
| 40 | 141,73 | 459,09 | 748,47 |
| 120 | 817,72 | 877,96 | 1.369,54 |

Há dispersão relevante: no cenário 120, A2 variou entre 545,58–1.058,14 ms,
B1 entre 467,05–1.238,77 ms e B2 entre 1.115,60–1.501,64 ms. Foi uma rodada
sequencial A2→B1→B2 num host compartilhado, sem controlar CPU/frequência ou
repetir a ordem inversa. Probes curtos do harness antigo ocorreram durante
a rodada. As medianas não são SLA, não comprovam uma razão fixa entre versões
e não devem fundamentar sozinhas uma alegação de melhoria futura.

O relato original da #109 citava aproximadamente 39 ms e 810 ms em B1.
Esta rodada confirma crescimento de custo e trabalho repetido, sem atribuir
àqueles valores uma reprodução exata: aquecimento, população, versão de
SQLite, ambiente e protocolo são explicitados aqui.

Chamadas em **um add perfilado** com 128 criações anteriores:

| Função | A2/v9 | B1/v10 | B2/v11 |
|---|---:|---:|---:|
| `validate_skill_timeline` | 8 | 16 | 20 |
| `validate_org_schema` | 0 | 16 | 22 |
| `validate_publication_schema` | 0 | 0 | 22 |
| `_get_validated` | 389 | 389 | 903 |

Em B2, esse perfil também observou quatro `repository_guard`, quatro consultas
de proteção, quatro recibos de política e 14 revalidações da política. O tempo
perfilado é separado das medianas: `_get_validated` acumulou 2,59 s num add
perfilado de 3,20 s. Tempos cumulativos das diferentes funções se sobrepõem;
não devem ser somados como parcelas independentes.

## Diagnóstico e próximo desenho

`repository_guard` valida a timeline; o guard consulta pendências e proteção;
`protected_organizations` valida a timeline novamente. Em `_load_validated_records`,
a timeline global alimenta carregamentos de registros antes do filtro por nome.
Os schemas organizacional e de publicação também são atestados repetidamente.
As contagens confirmam o mecanismo descrito na #109 na implementação atual.

O próximo desenho precisa definir uma observação estável, seu limite e sua
invalidação antes de reutilizar resultados. Não basta remover checks ou filtrar
registros de outros nomes: isso pode alterar a detecção de incoerência global.
Revalidação de política, identidade, pendências, troca do banco/WAL e efeitos
entre checagens continuam obrigatórias. Não foi introduzido cache, mudança de
schema ou relaxamento de guards nesta medição. A issue permanece aberta.

O cenário mede criações pessoais, sem bindings organizacionais, publicações,
adoções/revogações, concorrência, falha de processo ou adulteração. A proposta
de otimização terá que preservar esses outros contratos com regressões próprias.

## Correção do harness A2 anterior

O script `benchmark-skill-ledger.py` foi escrito para um fixture v9, mas seu
último `migrate()` acompanhou o padrão até v11. Ele passou a inserir adoções
na timeline nova enquanto contava a timeline v9 congelada. O probe encontrou
adoção ativa real com `timeline_events=0`.

RED: exigir timeline não vazia após a adoção real falhou. GREEN: fixar a
migração final explicitamente em v9, como já descrito naquele método, retornou
dois eventos, com adoção vigente, banco e raiz preservados. O relatório de
14/10 mantém seus números e hashes históricos; esta correção não os substitui
por novos tempos. A comparação acima usa o novo script e a timeline ativa de
cada revisão.
