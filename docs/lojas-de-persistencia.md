# As sete formas de persistência

O `state.db` é uma das lojas, não a única. O ERD §4 cataloga **sete formatos**
distintos, e a Tarefa 01 só constrói o primeiro — este documento existe para
que os outros seis não sejam esquecidos pelas tarefas que os implementam.

| Loja | Formato | Justificativa registrada | Tarefa |
|---|---|---|---|
| `state.db` | SQLite WAL, `synchronous=FULL` | Modelo relacional central, 10 tabelas | **01 ✅** |
| `cron/executions.db` | SQLite WAL, `synchronous=FULL` | Ledger de auditoria, **não** fila de retry | 14 |
| `cron/notepad.db` | SQLite WAL | Teto rígido: 16 KB/chave, 64 KB/job | 14 |
| `cron/jobs.json` | JSON + lock cross-process | 🟡 Inconsistência assumida: jobs em JSON enquanto execuções e notepad são SQLite | 14 |
| `cron/suggestions.json` | JSON atômico | Espelha `jobs.py` | 14 |
| `~/.kairos/skills/.usage.json` | JSON sidecar | *Não* frontmatter — evita pressão de conflito em skills bundled/hub | 09 |
| `~/.kairos/skills/.curator_ledger.jsonl` | JSONL | *"human-greppable, survives DB resets, rsync-friendly"* | 09 |
| `~/.kairos/.curator_backups/blobs/` | Content-addressed (sha256) | Dedup de conteúdo | 09 |
| `~/.kairos/auth.json` | JSON + flock | `credential_pool.<provider>[*]` | 15 |
| `~/.kairos/config.yaml` / `.env` | YAML / dotenv | Config / segredos | 15 |
| `projects.db` | SQLite | Ids de projeto explícitos (`p_<hex>`) | 15 |
| `plugin-data/<nome>/data.db` | SQLite WAL por plugin | Sobrevive a install/update/remove | 10 |

## Entidades sem persistência relacional

O ERD §5 lista as estruturas de domínio que vivem só em memória ou em JSON
não normalizado. Uma merece atenção na Tarefa 14:

> `job` (cron) é a **única entidade de domínio de primeira classe sem schema
> formal**. É um dict de ~35 chaves em `jobs.json`, persistida, mutada por
> múltiplos processos, com máquina de estados própria — e sem dataclass nem
> versionamento. Campo novo entra de forma aditiva e silenciosa.
