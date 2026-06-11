-- TIP-003 Migration 0003: dense similarity RPC for hybrid search
-- SECURITY DEFINER + service_role-only: called by the agent service, never by clients.

create or replace function match_kb_chunks(
    query_vec vector(1024),
    match_count int default 20
)
returns table (
    id uuid,
    doc_id text,
    content text,
    sparse_weights jsonb,
    metadata jsonb,
    similarity float
)
language sql
security definer
set search_path = public
as $$
    select
        kc.id,
        kc.doc_id,
        kc.content,
        kc.sparse_weights,
        kc.metadata,
        1 - (kc.dense_vec <=> query_vec) as similarity
    from kb_chunks kc
    where kc.dense_vec is not null
    order by kc.dense_vec <=> query_vec
    limit match_count;
$$;

revoke execute on function match_kb_chunks(vector, int) from public, anon, authenticated;
grant execute on function match_kb_chunks(vector, int) to service_role;
