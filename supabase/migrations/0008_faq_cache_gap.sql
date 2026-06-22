-- TIP-015 Migration 0008: semantic faq cache + knowledge-gap events
-- Both tables are SERVICE-ROLE ONLY (RLS enabled, no policies → anon/authenticated
-- denied; the agent service bypasses RLS). Cache is intentionally faq-only and
-- never stores PII (enforced at the app layer).

-- ============ faq_cache ============
-- Cache key is (cosine ≥ 0.93) AND intent='faq' AND entities match AND
-- kb_version = current. kb_version in the key makes a KB re-ingest (which bumps
-- kb_meta.kb_version) auto-invalidate every old entry — no deletion required.
create table faq_cache (
    id              uuid primary key default gen_random_uuid(),
    query_embedding vector(1024) not null,
    intent          text not null default 'faq',
    entities        jsonb not null default '[]',   -- sorted canonical entity tokens
    kb_version      int not null,
    reply           text not null,                 -- final guardrailed reply
    citations       jsonb not null default '[]',
    hit_count       int not null default 0,
    last_hit_at     timestamptz,
    created_at      timestamptz not null default now()
);

create index idx_faq_cache_vec on faq_cache
    using ivfflat (query_embedding vector_cosine_ops) with (lists = 100);
create index idx_faq_cache_kb_version on faq_cache (kb_version);

-- ============ kb_gap_events ============
-- One row per faq turn the RAG pipeline could NOT answer (no chunks or
-- groundedness=false). query is MASKED (no raw PII). Clustered in-app for the
-- console knowledge-gaps view.
create table kb_gap_events (
    id         uuid primary key default gen_random_uuid(),
    query      text not null,                       -- masked
    reason     text not null,                       -- 'no_chunks' | 'groundedness_false'
    embedding  vector(1024),
    created_at timestamptz not null default now()
);

create index idx_kb_gap_events_created on kb_gap_events (created_at desc);

-- ============ RLS — service-role only ============
alter table faq_cache     enable row level security;
alter table kb_gap_events enable row level security;
-- no policies → anon & authenticated denied; service_role bypasses RLS.

-- ============ cache lookup RPC (service-role) ============
-- Returns the nearest faq_cache rows for the CURRENT kb_version + intent='faq'.
-- The app layer applies the final safety checks (cosine ≥ 0.93, entity match,
-- 24h TTL) over these candidates.
create or replace function match_faq_cache(
    query_vec   vector(1024),
    kb_ver      int,
    match_count int default 5
)
returns table (
    id         uuid,
    entities   jsonb,
    kb_version int,
    reply      text,
    citations  jsonb,
    hit_count  int,
    created_at timestamptz,
    similarity float
)
language sql
security definer
set search_path = public
as $$
    select
        fc.id,
        fc.entities,
        fc.kb_version,
        fc.reply,
        fc.citations,
        fc.hit_count,
        fc.created_at,
        1 - (fc.query_embedding <=> query_vec) as similarity
    from faq_cache fc
    where fc.intent = 'faq' and fc.kb_version = kb_ver
    order by fc.query_embedding <=> query_vec
    limit match_count;
$$;

revoke execute on function match_faq_cache(vector, int, int) from public, anon, authenticated;
grant execute on function match_faq_cache(vector, int, int) to service_role;
