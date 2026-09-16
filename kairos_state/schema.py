"""Schema relacional do ``state.db`` do Kairos.

Reconstruído a partir de ``_reversa_sdd/erd-complete.md`` §1-3 e
``_reversa_sdd/data-dictionary.md`` §6 (Tarefa 01 do plano de reconstrução).

Versionamento
-------------
O legado chegou a ``SCHEMA_VERSION = 26`` por 26 migrações sucessivas. O
Kairos é reimplementação, não fork: nasce com a **forma final** daquele
schema, e portanto com sua própria versão ``1``. ``LEGACY_SHAPE_VERSION``
registra a que versão do legado esta forma corresponde, para que a
rastreabilidade não se perca.

As duas divergências deliberadas em relação ao legado estão marcadas com
``DIVERGÊNCIA`` no corpo do SQL e justificadas em ``docs/decisoes.md``.
"""

from __future__ import annotations

SCHEMA_VERSION = 5
LEGACY_SHAPE_VERSION = 26
FTS_STORAGE_VERSION = 1

MAX_SAFE_RESUME_MESSAGES = 20_000
MAX_SAFE_EXPORT_MESSAGES = 20_000
MAX_FTS5_QUERY_CHARS = 2_048

# Chaves de estado em ``state_meta``.
FTS_STALE_KEY = "fts_stale"
FTS_CJK_STALE_KEY = "fts_cjk_stale"
FTS_CJK_HIGH_WATER_KEY = "fts_cjk_rebuild_high_water"


# ---------------------------------------------------------------------------
# Tabelas
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS system_prompts (
    hash    TEXT PRIMARY KEY,
    prompt  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id                                  TEXT PRIMARY KEY,
    source                              TEXT NOT NULL,
    user_id                             TEXT,
    session_key                         TEXT,
    chat_id                             TEXT,
    chat_type                           TEXT,
    thread_id                           TEXT,
    display_name                        TEXT,
    origin_json                         TEXT,
    expiry_finalized                    INTEGER DEFAULT 0,
    model                               TEXT,
    model_config                        TEXT,
    system_prompt                       TEXT,
    system_prompt_hash                  TEXT REFERENCES system_prompts(hash),
    parent_session_id                   TEXT REFERENCES sessions(id),
    started_at                          REAL NOT NULL,
    ended_at                            REAL,
    end_reason                          TEXT,
    message_count                       INTEGER DEFAULT 0,
    tool_call_count                     INTEGER DEFAULT 0,
    api_call_count                      INTEGER DEFAULT 0,
    input_tokens                        INTEGER DEFAULT 0,
    output_tokens                       INTEGER DEFAULT 0,
    cache_read_tokens                   INTEGER DEFAULT 0,
    cache_write_tokens                  INTEGER DEFAULT 0,
    reasoning_tokens                    INTEGER DEFAULT 0,
    cwd                                 TEXT,
    git_branch                          TEXT,
    git_repo_root                       TEXT,
    git_metadata_generation             INTEGER NOT NULL DEFAULT 0,
    billing_provider                    TEXT,
    billing_base_url                    TEXT,
    billing_mode                        TEXT,
    estimated_cost_usd                  REAL,
    actual_cost_usd                     REAL,
    cost_status                         TEXT,
    cost_source                         TEXT,
    pricing_version                     TEXT,
    title                               TEXT,
    title_source                        TEXT,
    last_activity_at                    REAL,
    last_activity_description           TEXT,
    last_activity_provenance            TEXT,
    handoff_state                       TEXT,
    handoff_platform                    TEXT,
    handoff_error                       TEXT,
    compression_failure_cooldown_until  REAL,
    compression_failure_error           TEXT,
    compression_fallback_streak         INTEGER NOT NULL DEFAULT 0,
    compression_ineffective_count       INTEGER NOT NULL DEFAULT 0,
    profile_name                        TEXT,
    rewind_count                        INTEGER NOT NULL DEFAULT 0,
    archived                            INTEGER NOT NULL DEFAULT 0,
    pinned                              INTEGER NOT NULL DEFAULT 0,
    hidden                              INTEGER NOT NULL DEFAULT 0,
    last_read_at                        REAL
);

-- Organização da biblioteca de sessões. Separada da linha canônica para que
-- tags possam evoluir sem alterar a forma histórica da tabela `sessions`.
CREATE TABLE IF NOT EXISTS session_tags (
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    PRIMARY KEY (session_id, tag)
);

CREATE INDEX IF NOT EXISTS idx_session_tags_tag ON session_tags(tag);

-- ``api_content`` é o SIDECAR: o que vai para a API, separado do que é
-- exibido. É o mecanismo que sustenta a Lei 1 (o cache de prompt por
-- conversa é sagrado) — reescrever ``content`` para exibição não altera o
-- prefixo enviado ao provedor.
--
-- As três visibilidades vivem em ``active``/``compacted``:
--   active=1 compacted=0 → ativa      (busca ✅, contexto ✅)
--   active=0 compacted=1 → arquivada  (busca ✅, contexto ❌)
--   active=0 compacted=0 → rebobinada (busca ❌, contexto ❌)
CREATE TABLE IF NOT EXISTS messages (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id             TEXT NOT NULL REFERENCES sessions(id),
    role                   TEXT NOT NULL,
    content                TEXT,
    api_content            TEXT,
    tool_call_id           TEXT,
    tool_calls             TEXT,
    tool_name              TEXT,
    effect_disposition     TEXT,
    timestamp              REAL NOT NULL,
    token_count            INTEGER,
    finish_reason          TEXT,
    reasoning              TEXT,
    reasoning_content      TEXT,
    reasoning_details      TEXT,
    codex_reasoning_items  TEXT,
    codex_message_items    TEXT,
    platform_message_id    TEXT,
    observed               INTEGER DEFAULT 0,
    active                 INTEGER NOT NULL DEFAULT 1,
    compacted              INTEGER NOT NULL DEFAULT 0,
    display_kind           TEXT,
    display_metadata       TEXT
);

CREATE TABLE IF NOT EXISTS session_model_usage (
    session_id          TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    model               TEXT NOT NULL,
    billing_provider    TEXT NOT NULL,
    billing_base_url    TEXT NOT NULL,
    billing_mode        TEXT NOT NULL,
    task                TEXT NOT NULL,
    api_call_count      INTEGER DEFAULT 0,
    input_tokens        INTEGER DEFAULT 0,
    output_tokens       INTEGER DEFAULT 0,
    cache_read_tokens   INTEGER DEFAULT 0,
    cache_write_tokens  INTEGER DEFAULT 0,
    reasoning_tokens    INTEGER DEFAULT 0,
    estimated_cost_usd  REAL,
    actual_cost_usd     REAL,
    cost_status         TEXT,
    cost_source         TEXT,
    first_seen          REAL,
    last_seen           REAL,
    PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task)
);

CREATE TABLE IF NOT EXISTS compression_locks (
    session_id   TEXT PRIMARY KEY REFERENCES sessions(id),
    holder       TEXT,
    acquired_at  REAL,
    expires_at   REAL
);

-- Materialização durável do turn lease (ADR 004). A chave é a identidade
-- DURÁVEL da conversa — ``sessions.id`` —, nunca a chave de roteamento.
-- Ver a nota de DIVERGÊNCIA em ``gateway_routing``.
CREATE TABLE IF NOT EXISTS session_turn_leases (
    conversation_id  TEXT PRIMARY KEY REFERENCES sessions(id),
    holder           TEXT,
    acquired_at      REAL,
    expires_at       REAL
);

CREATE TABLE IF NOT EXISTS async_delegations (
    delegation_id         TEXT PRIMARY KEY,
    origin_session        TEXT NOT NULL,
    origin_ui_session_id  TEXT NOT NULL DEFAULT '',
    parent_session_id     TEXT,
    state                 TEXT NOT NULL,
    dispatched_at         REAL NOT NULL,
    completed_at          REAL,
    updated_at            REAL NOT NULL,
    event_json            TEXT,
    result_json           TEXT,
    delivery_state        TEXT NOT NULL DEFAULT 'pending',
    delivery_attempts     INTEGER NOT NULL DEFAULT 0,
    delivered_at          REAL,
    owner_pid             INTEGER,
    owner_started_at      INTEGER,
    task_json             TEXT,
    delivery_claim        TEXT,
    delivery_claimed_at   REAL
);

-- DIVERGÊNCIA DELIBERADA (decisão da Tarefa 01; ERD §2 e §6).
--
-- No legado, o ``session_id`` ficava enterrado dentro de ``entry_json`` e
-- não havia FK: ``session_key`` órfão era possível, e a relação N:1 entre
-- a chave de ROTEAMENTO e a identidade DURÁVEL era invisível ao banco.
-- Foi essa invisibilidade que produziu o #64934: os guards eram chaveados
-- por ``session_key`` enquanto o transcript pertence a ``sessions.id``, e
-- ``switch_session()`` torna a relação N:1 — nenhum guard por chave de
-- roteamento enxerga a colisão.
--
-- O Kairos promove ``session_id`` a coluna de primeira classe com FK.
-- A regra que isso torna verificável: **o lock fica do lado do DADO**
-- (``session_turn_leases.conversation_id`` → ``sessions.id``), nunca do
-- lado da chave de coordenação.
CREATE TABLE IF NOT EXISTS gateway_routing (
    scope        TEXT NOT NULL,
    session_key  TEXT NOT NULL,
    session_id   TEXT REFERENCES sessions(id),
    entry_json   TEXT,
    updated_at   REAL,
    PRIMARY KEY (scope, session_key)
);

CREATE TABLE IF NOT EXISTS gateway_hygiene_state (
    session_key    TEXT PRIMARY KEY,
    failure_streak INTEGER
);

-- RF-19. Obrigação de entrega com liveness do dono: o par
-- ``owner_pid`` + ``owner_started_at`` é a mesma convenção do ledger de cron.
-- O PID sozinho não serve — o SO o recicla, e uma obrigação seria dada como
-- viva por um processo que só herdou o número.
CREATE TABLE IF NOT EXISTS delivery_obligations (
    obligation_id     TEXT PRIMARY KEY,
    session_id        TEXT REFERENCES sessions(id),
    target            TEXT NOT NULL,
    payload_json      TEXT,
    state             TEXT NOT NULL DEFAULT 'pending'
                      CHECK(state IN ('pending','claimed','delivered','abandoned')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    owner_pid         INTEGER,
    owner_started_at  INTEGER,
    created_at        REAL NOT NULL,
    updated_at        REAL,
    delivered_at      REAL
);

CREATE TABLE IF NOT EXISTS state_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_source    ON sessions(source);
CREATE INDEX IF NOT EXISTS idx_sessions_source_id ON sessions(source, id);
CREATE INDEX IF NOT EXISTS idx_sessions_parent    ON sessions(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started   ON sessions(started_at DESC);

CREATE INDEX IF NOT EXISTS idx_messages_session    ON messages(session_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id, id);

-- Índice PARCIAL para o scan de insights. Pode viver no conjunto imediato
-- porque ``role`` e ``tool_calls`` são colunas base.
CREATE INDEX IF NOT EXISTS idx_messages_assistant_calls_by_session
    ON messages(session_id) WHERE role = 'assistant' AND tool_calls IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_compression_locks_expires   ON compression_locks(expires_at);
CREATE INDEX IF NOT EXISTS idx_session_turn_leases_expires ON session_turn_leases(expires_at);

CREATE INDEX IF NOT EXISTS idx_session_model_usage_session ON session_model_usage(session_id);
CREATE INDEX IF NOT EXISTS idx_session_model_usage_model   ON session_model_usage(model);

CREATE INDEX IF NOT EXISTS idx_async_delegations_delivery
    ON async_delegations(delivery_state, completed_at);

-- Consequência da DIVERGÊNCIA acima: a resolução roteamento → sessão passa
-- a ser indexável.
CREATE INDEX IF NOT EXISTS idx_gateway_routing_session ON gateway_routing(session_id);

CREATE INDEX IF NOT EXISTS idx_delivery_obligations_state
    ON delivery_obligations(state, created_at);
"""


# Índices criados após o schema base, fora do caminho crítico de boot.
DEFERRED_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_messages_session_active
    ON messages(session_id, active, timestamp);
CREATE INDEX IF NOT EXISTS idx_messages_active_null
    ON messages(active) WHERE active IS NULL;
"""


# ---------------------------------------------------------------------------
# FTS — três índices paralelos
# ---------------------------------------------------------------------------
#
# Os três indexam as mesmas colunas (``content``, ``tool_name``,
# ``tool_calls``) e servem rotas de busca diferentes:
#
#   messages_fts          unicode61       external content sobre ``messages``
#   messages_fts_trigram  trigram         view, ``role <> 'tool'``
#   messages_fts_cjk      cjk_unicode61   view, ``role <> 'tool'`` — extensão nativa
#
# As duas views excluem ``role='tool'`` porque saída de ferramenta domina o
# volume e polui o resultado de busca do usuário.
#
# O índice CJK depende da extensão compilada ``fts5_cjk`` (Tarefa 04). Sua
# criação é opcional e falha-aberto: sem a extensão, restam FTS5 base,
# trigram e ``LIKE`` como fallback final.

FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, tool_name, tool_calls,
    content='messages', content_rowid='id',
    tokenize='unicode61'
);

CREATE VIEW IF NOT EXISTS messages_fts_trigram_src AS
    SELECT id, content, tool_name, tool_calls FROM messages WHERE role <> 'tool';

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_trigram USING fts5(
    content, tool_name, tool_calls,
    tokenize='trigram'
);
"""

FTS_CJK_SQL = """
CREATE VIEW IF NOT EXISTS messages_fts_cjk_src AS
    SELECT id, content, tool_name, tool_calls FROM messages WHERE role <> 'tool';

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts_cjk USING fts5(
    content, tool_name, tool_calls,
    tokenize='cjk_unicode61'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, tool_name, tool_calls)
        VALUES ('delete', old.id, old.content, old.tool_name, old.tool_calls);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, tool_name, tool_calls)
        VALUES ('delete', old.id, old.content, old.tool_name, old.tool_calls);
    INSERT INTO messages_fts(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_insert AFTER INSERT ON messages
WHEN new.role <> 'tool' BEGIN
    INSERT INTO messages_fts_trigram(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;

-- ATENÇÃO: ``messages_fts_trigram`` é uma tabela FTS5 STANDALONE, não
-- external-content. O comando ``INSERT INTO t(t, ...) VALUES('delete', ...)``
-- só existe para tabelas external-content/contentless; numa standalone ele
-- devolve "SQL logic error". Aqui a remoção é um DELETE comum.
CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_delete AFTER DELETE ON messages
WHEN old.role <> 'tool' BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_trigram_update AFTER UPDATE ON messages
WHEN new.role <> 'tool' BEGIN
    DELETE FROM messages_fts_trigram WHERE rowid = old.id;
    INSERT INTO messages_fts_trigram(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;
"""

# O trigger de INSERT do CJK consulta o high-water antes de indexar. É o que
# permite reconstruir o índice incrementalmente sem indexar duas vezes: a
# reconstrução varre até o high-water, e o trigger cobre o que vem depois.
FTS_CJK_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS messages_fts_cjk_insert AFTER INSERT ON messages
WHEN new.role <> 'tool'
 AND new.id > COALESCE(
        (SELECT CAST(value AS INTEGER) FROM state_meta WHERE key = 'fts_cjk_rebuild_high_water'),
        -1)
BEGIN
    INSERT INTO messages_fts_cjk(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;

-- Standalone, como o trigram: DELETE comum, não o comando 'delete'.
CREATE TRIGGER IF NOT EXISTS messages_fts_cjk_delete AFTER DELETE ON messages
WHEN old.role <> 'tool' BEGIN
    DELETE FROM messages_fts_cjk WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS messages_fts_cjk_update AFTER UPDATE ON messages
WHEN new.role <> 'tool' BEGIN
    DELETE FROM messages_fts_cjk WHERE rowid = old.id;
    INSERT INTO messages_fts_cjk(rowid, content, tool_name, tool_calls)
        VALUES (new.id, new.content, new.tool_name, new.tool_calls);
END;
"""
