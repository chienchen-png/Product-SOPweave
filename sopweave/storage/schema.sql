-- SOPweave - SQLite Schema
-- Phase 2: all 8 tables per PRD §5.3

CREATE TABLE IF NOT EXISTS projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    year            INTEGER,
    quarter         TEXT,
    work_dir        TEXT NOT NULL,
    default_agent   TEXT,
    default_model   TEXT,
    work_mode       TEXT NOT NULL DEFAULT 'manual',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id)
);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL,
    content         TEXT,
    agent_id        TEXT,
    model_id        TEXT,
    tokens_used     INTEGER,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

CREATE TABLE IF NOT EXISTS models (
    id              TEXT PRIMARY KEY,
    provider_id     TEXT,
    name            TEXT NOT NULL,
    api_url         TEXT NOT NULL,
    api_key         TEXT,
    type            TEXT NOT NULL,
    capabilities    TEXT,
    api_model       TEXT,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    request_options_json TEXT NOT NULL DEFAULT '{}',
    context_window  INTEGER DEFAULT 8192,
    is_default      INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'unknown',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (provider_id) REFERENCES model_providers(id)
);

CREATE TABLE IF NOT EXISTS deleted_presets (
    id              TEXT PRIMARY KEY,
    deleted_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_capability_checks (
    model_id       TEXT NOT NULL,
    check_name     TEXT NOT NULL,
    status         TEXT NOT NULL,
    detail_json    TEXT NOT NULL DEFAULT '{}',
    checked_at     TEXT NOT NULL,
    PRIMARY KEY (model_id, check_name)
);

CREATE TABLE IF NOT EXISTS model_providers (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    api_url         TEXT NOT NULL,
    api_key         TEXT,
    api_key_url     TEXT,
    description     TEXT,
    provider_kind   TEXT NOT NULL DEFAULT 'openai_compatible',
    api_mode        TEXT NOT NULL DEFAULT 'chat_completions',
    options_json    TEXT NOT NULL DEFAULT '{}',
    is_preset       INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'available',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT,
    description     TEXT,
    dependencies    TEXT,
    enabled         INTEGER DEFAULT 1,
    system_prompt   TEXT,
    tools           TEXT,
    skill_path      TEXT
);

CREATE TABLE IF NOT EXISTS token_usage (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT,
    conversation_id TEXT,
    message_id      TEXT,
    agent_id        TEXT,
    model_id        TEXT,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    reasoning_tokens INTEGER,
    total_tokens    INTEGER,
    call_id         TEXT,
    run_id          TEXT,
    execution_id    TEXT,
    boot_id         TEXT,
    provider_id     TEXT,
    provider_name   TEXT,
    provider_kind   TEXT,
    model_name      TEXT,
    api_model       TEXT,
    request_state   TEXT,
    usage_state     TEXT,
    provider_request_id TEXT,
    finished_at     TEXT,
    duration_ms     INTEGER,
    usage_json      TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_usage_state (
    id INTEGER PRIMARY KEY CHECK(id=1), started_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tool_calls (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    conversation_id TEXT,
    message_id      TEXT,
    tool_name       TEXT NOT NULL,
    parameters_summary TEXT,
    approval_status TEXT,
    status          TEXT,
    result_summary  TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS local_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT,
    event_type      TEXT NOT NULL,
    event_summary   TEXT,
    status          TEXT,
    detail          TEXT,
    created_at      TEXT NOT NULL
);

-- ============================================================================
-- Phase 7.1: Runtime tables (runs, run_events, plans, plan_steps, interactions)
-- ============================================================================

CREATE TABLE IF NOT EXISTS runs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT,
    conversation_id TEXT,
    user_message_id TEXT,
    agent_id        TEXT,
    model_id        TEXT,
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'starting',
    plan_version    INTEGER DEFAULT 0,
    progress        REAL DEFAULT 0,
    progress_message TEXT,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS run_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    payload_json    TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);
CREATE INDEX IF NOT EXISTS idx_run_events_run_seq ON run_events(run_id, seq);

CREATE TABLE IF NOT EXISTS agent_session_items (
    session_id      TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    item_json       TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_agent_session_items_latest
    ON agent_session_items(session_id, seq DESC);

CREATE TABLE IF NOT EXISTS agent_run_checkpoints (
    run_id          TEXT PRIMARY KEY,
    checkpoint_json TEXT NOT NULL,
    sdk_version     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ready',
    lease_token     TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plans (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    total_estimated_steps INTEGER DEFAULT 0,
    total_weight    REAL DEFAULT 100,
    summary         TEXT,
    is_confirmed    INTEGER DEFAULT 0,
    confirmed_at    TEXT,
    is_modified_by_user INTEGER DEFAULT 0,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS plan_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id         TEXT NOT NULL,
    step_id         TEXT NOT NULL,
    parent_step_id  TEXT,
    title           TEXT NOT NULL,
    description     TEXT,
    weight          REAL DEFAULT 25,
    status          TEXT DEFAULT 'pending',
    tool_name       TEXT,
    output_summary  TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    sort_order      INTEGER DEFAULT 0,
    FOREIGN KEY (plan_id) REFERENCES plans(id)
);
CREATE INDEX IF NOT EXISTS idx_plan_steps_plan ON plan_steps(plan_id);

CREATE TABLE IF NOT EXISTS interactions (
    id              TEXT PRIMARY KEY,
    run_id          TEXT NOT NULL,
    tool_call_id    TEXT,
    interaction_type TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    options_json    TEXT,
    user_response   TEXT,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT NOT NULL,
    responded_at    TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id)
);

-- ============================================================================
-- Phase 14A: Project data plane (Excel → SQLite structured intermediate state)
-- ============================================================================

CREATE TABLE IF NOT EXISTS project_data_files (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    source_path     TEXT NOT NULL,
    file_name       TEXT NOT NULL,
    file_hash       TEXT,
    format          TEXT NOT NULL,
    sheet_count     INTEGER DEFAULT 0,
    total_rows      INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'importing',
    error_message   TEXT,
    meta_json       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_data_files_project
    ON project_data_files(project_id);

CREATE TABLE IF NOT EXISTS project_data_sheets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         TEXT NOT NULL,
    sheet_name      TEXT NOT NULL,
    sheet_index     INTEGER NOT NULL,
    table_name      TEXT NOT NULL,
    row_count       INTEGER DEFAULT 0,
    col_count       INTEGER DEFAULT 0,
    headers_json    TEXT
);
CREATE INDEX IF NOT EXISTS idx_project_data_sheets_file
    ON project_data_sheets(file_id);

-- Phase 14C: cell-level change log (diff preview + precise write-back source)
-- Phase 17 P5: + col_name/key_value（字段级+行定位键日志——写回/展示的精确依据）
CREATE TABLE IF NOT EXISTS project_data_changes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id         TEXT NOT NULL,
    sheet_name      TEXT NOT NULL,
    row_index       INTEGER NOT NULL,
    col_index       INTEGER,
    col_name        TEXT,
    key_value       TEXT,
    old_value       TEXT,
    new_value       TEXT,
    op_type         TEXT NOT NULL,
    run_id          TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_data_changes_file
    ON project_data_changes(file_id);

-- ============================================================================
-- 工作台总览 D1：app_sessions 会话计时器（软件打开时长口径，v1.2 定稿）
-- 本质：打开即计时、关闭即结束；纯离线、从安装起累计（仅用于演示）。
-- 每次后端进程启动插一行；atexit 写 end_at；强杀由下次启动 stale 兜底。
-- ============================================================================
CREATE TABLE IF NOT EXISTS app_sessions (
    id            TEXT PRIMARY KEY,
    start_at      TEXT NOT NULL,          -- 后端进程启动时间（iso_now UTC）
    last_seen_at  TEXT,                   -- 心跳最后时间（崩溃兜底）
    end_at        TEXT,                   -- 进程退出时间（atexit 写）
    status        TEXT DEFAULT 'open'     -- open / closed / stale
);
CREATE INDEX IF NOT EXISTS idx_app_sessions_start ON app_sessions(start_at);

-- User SOP library: immutable revisions, reviewable drafts, durable run leases.
CREATE TABLE IF NOT EXISTS workspace_drafts (
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('chat','recon','finmod')),
    state_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data_revision TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(project_id,kind)
);
CREATE INDEX IF NOT EXISTS idx_runs_project_updated ON runs(project_id,updated_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_run_events_type_seq ON run_events(run_id,event_type,seq DESC);
CREATE TABLE IF NOT EXISTS user_skills (
    skill_id TEXT PRIMARY KEY CHECK(length(skill_id)=37 AND substr(skill_id,1,5)='user-' AND substr(skill_id,6) NOT GLOB '*[^0-9a-f]*'),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','deleted')),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    current_version INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS user_skill_versions (
    skill_id TEXT NOT NULL REFERENCES user_skills(skill_id),
    version INTEGER NOT NULL CHECK(version>0),
    spec_json TEXT NOT NULL,
    description TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    content_digest TEXT NOT NULL,
    package_relative_path TEXT NOT NULL,
    policy_json TEXT NOT NULL,
    source_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(skill_id,version)
);
CREATE TABLE IF NOT EXISTS skill_drafts (
    draft_id TEXT PRIMARY KEY,
    draft_revision INTEGER NOT NULL DEFAULT 1,
    base_skill_id TEXT REFERENCES user_skills(skill_id),
    base_version INTEGER,
    state TEXT NOT NULL DEFAULT 'created',
    spec_json TEXT NOT NULL DEFAULT 'null',
    source_json TEXT NOT NULL DEFAULT '[]',
    questions_json TEXT NOT NULL DEFAULT '[]',
    answers_json TEXT NOT NULL DEFAULT '[]',
    generated_digest TEXT NOT NULL DEFAULT '',
    job_id TEXT,
    job_status_json TEXT NOT NULL DEFAULT '{}',
    publication_ref TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_run_refs (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    skill_id TEXT NOT NULL,
    version TEXT NOT NULL,
    origin TEXT NOT NULL CHECK(origin IN ('builtin','user')),
    content_digest TEXT NOT NULL,
    lease_state TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id,skill_id,version)
);
CREATE INDEX IF NOT EXISTS idx_skill_run_refs_active ON skill_run_refs(skill_id,lease_state);
CREATE TABLE IF NOT EXISTS skill_run_contexts (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS skill_catalog_state (
    id INTEGER PRIMARY KEY CHECK(id=1),
    generation INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO skill_catalog_state(id,generation) VALUES(1,0);
CREATE TABLE IF NOT EXISTS skill_publications (
    idempotency_key TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL UNIQUE REFERENCES skill_drafts(draft_id),
    skill_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    confirmed_digest TEXT NOT NULL,
    FOREIGN KEY(skill_id,version) REFERENCES user_skill_versions(skill_id,version)
);
