-- TIP-002 Migration 0001: extensions + 14 tables + indexes
-- Source of truth: BLUEPRINT-XeCare.md §4 (10 core tables)
-- + branches, service_slots, kb_meta, parts_orders (approved in TIP-002)

create extension if not exists vector;

-- ============ Core tables (Blueprint §4) ============

-- phone_hash = sha256(PHONE_HASH_SALT + phone_E164), computed at app layer.
-- No real phone column exists anywhere in the schema (Blueprint §6.1).
create table customer_profiles (
    id            uuid primary key default gen_random_uuid(),
    phone_hash    text unique not null,
    display_name  text,
    vehicles      jsonb not null default '[]',
    -- vehicles: [{type:'motorbike'|'car', model, year, last_km, last_service_at}]
    facts         jsonb not null default '{}',
    last_summary  text,
    updated_at    timestamptz not null default now()
);

create table conversations (
    id           uuid primary key default gen_random_uuid(),
    customer_id  uuid references customer_profiles(id),
    mode         text not null default 'agent' check (mode in ('agent','human')),
    channel      text,
    started_at   timestamptz not null default now(),
    closed_at    timestamptz,
    resolution   text
);

-- content: raw (may contain PII) — service role only.
-- content_masked: the only version clients may read (via messages_public view).
create table messages (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid not null references conversations(id),
    sender          text not null check (sender in ('customer','agent','staff')),
    content         text,
    content_masked  text,
    created_at      timestamptz not null default now()
);

create table trace_events (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid references conversations(id),
    message_id      uuid references messages(id),
    step_type       text not null check (step_type in
        ('router','retrieval','tool_call','guardrail_in',
         'guardrail_out','llm_call','cache_hit','escalation')),
    payload         jsonb,
    latency_ms      int,
    cost_usd        numeric,
    policy_version  int,
    prompt_version  int,
    created_at      timestamptz not null default now()
);

create table kb_chunks (
    id             uuid primary key default gen_random_uuid(),
    doc_id         text,
    content        text,
    dense_vec      vector(1024),
    sparse_weights jsonb,
    metadata       jsonb
);

create table eval_runs (
    id             uuid primary key default gen_random_uuid(),
    git_sha        text,
    prompt_version int,
    suite          text,
    -- 'golden','ragas','adversarial_critical','adversarial_quality'
    total          int,
    passed         int,
    metrics        jsonb,
    created_at     timestamptz not null default now()
);

create table eval_cases (
    id          uuid primary key default gen_random_uuid(),
    suite       text,
    severity    text check (severity in ('critical','quality')),
    input       jsonb,
    expectation jsonb,
    active      bool not null default true
);

create table prompt_registry (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    version    int not null,
    content    text,
    active     bool not null default false,
    created_at timestamptz not null default now()
);

create table policy_registry (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    version    int not null,
    rules      jsonb,
    active     bool not null default false,
    created_at timestamptz not null default now()
);

create table tickets (
    id              uuid primary key default gen_random_uuid(),
    conversation_id uuid references conversations(id),
    type            text not null check (type in ('booking','rescue','complaint','after_hours')),
    priority        text not null default 'normal' check (priority in ('normal','high','urgent')),
    payload         jsonb,
    status          text not null default 'open' check (status in ('open','claimed','resolved','cancelled')),
    created_at      timestamptz not null default now()
);

-- ============ Support tables (approved in TIP-002) ============

-- Cache invalidation marker (Blueprint TIP-015)
create table kb_meta (
    key   text primary key,
    value jsonb
);

insert into kb_meta (key, value) values ('kb_version', '1');

create table branches (
    id         uuid primary key default gen_random_uuid(),
    name       text not null,
    address    text,
    district   text,
    open_hours jsonb
);

create table service_slots (
    id           uuid primary key default gen_random_uuid(),
    branch_id    uuid not null references branches(id),
    starts_at    timestamptz not null,
    vehicle_type text not null check (vehicle_type in ('motorbike','car')),
    capacity     int not null default 1,
    booked       int not null default 0
);

create table parts_orders (
    id          uuid primary key default gen_random_uuid(),
    customer_id uuid references customer_profiles(id),
    items       jsonb,
    status      text not null check (status in ('processing','shipped','delivered','cancelled')),
    total_vnd   bigint,
    paid        bool not null default false,
    created_at  timestamptz not null default now()
);

-- ============ Indexes ============

create index idx_messages_conversation_created on messages (conversation_id, created_at);
create index idx_trace_events_conversation on trace_events (conversation_id);
create index idx_trace_events_step_type on trace_events (step_type);
create index idx_eval_runs_git_sha on eval_runs (git_sha);
create index idx_tickets_status_priority on tickets (status, priority);
create index idx_kb_chunks_dense_vec on kb_chunks
    using ivfflat (dense_vec vector_cosine_ops) with (lists = 100);
