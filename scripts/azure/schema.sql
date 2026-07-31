-- Outcome Agent must-have schema (Azure PostgreSQL)
-- case / events / facts / learning / mailbox / graph outbox

CREATE TABLE IF NOT EXISTS oa_cases (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    invoice_no TEXT,
    customer_name TEXT,
    customer_email TEXT,
    subject_token TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    amount DOUBLE PRECISION,
    world_json JSONB NOT NULL DEFAULT '{}',
    dialogue_json JSONB NOT NULL DEFAULT '{}',
    budget_json JSONB NOT NULL DEFAULT '{}',
    goals_json JSONB NOT NULL DEFAULT '{}',
    commitments_json JSONB NOT NULL DEFAULT '[]',
    blockers_json JSONB NOT NULL DEFAULT '[]',
    failed_asks_json JSONB NOT NULL DEFAULT '[]',
    escalation_json JSONB,
    last_decision_json JSONB,
    last_outreach_at TIMESTAMPTZ,
    next_action_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oa_events (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES oa_cases(id) ON DELETE CASCADE,
    at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind TEXT NOT NULL,
    detail_json JSONB NOT NULL DEFAULT '{}',
    principles_json JSONB NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_oa_events_case_at ON oa_events(case_id, at);

CREATE TABLE IF NOT EXISTS oa_memory_facts (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES oa_cases(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value_json JSONB NOT NULL,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    source_event_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    superseded_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_oa_facts_case_active ON oa_memory_facts(case_id) WHERE status = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS idx_oa_facts_active_unique
    ON oa_memory_facts(case_id, kind, key) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS oa_learning (
    id TEXT PRIMARY KEY,
    ask_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    tactic TEXT NOT NULL,
    objective TEXT,
    outcome TEXT NOT NULL,
    produced_commitment_id TEXT,
    weight_delta DOUBLE PRECISION NOT NULL DEFAULT 0,
    scored_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS oa_tactic_weights (
    tactic TEXT NOT NULL,
    objective TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 0,
    PRIMARY KEY (tactic, objective)
);

CREATE TABLE IF NOT EXISTS oa_mailbox_messages (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL REFERENCES oa_cases(id) ON DELETE CASCADE,
    direction TEXT NOT NULL CHECK (direction IN ('outbound', 'inbound')),
    provider_message_id TEXT,
    subject TEXT,
    body_preview TEXT,
    body_blob_path TEXT,
    meta_json JSONB NOT NULL DEFAULT '{}',
    at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_oa_mailbox_case ON oa_mailbox_messages(case_id, at);

-- Postgres-first graph sync: write here, flush to Cosmos Gremlin
CREATE TABLE IF NOT EXISTS oa_graph_outbox (
    id TEXT PRIMARY KEY,
    op TEXT NOT NULL,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INT NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_oa_graph_outbox_pending ON oa_graph_outbox(status, created_at)
    WHERE status = 'pending';
