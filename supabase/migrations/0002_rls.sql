-- TIP-002 Migration 0002: RLS + messages_public view + grants + realtime
-- Access model (Blueprint §4 RLS + TIP-002 §2):
--   anon (customer widget): conversation_id is a capability token — knowing the
--     UUID grants read on that conversation. Masked message content only.
--   authenticated (staff console): read everything; update conversations.mode,
--     tickets.status; insert messages.
--   service_role (agent service): bypasses RLS — no policies needed.

-- ============ Enable RLS on all tables ============

alter table customer_profiles enable row level security;
alter table conversations     enable row level security;
alter table messages          enable row level security;
alter table trace_events      enable row level security;
alter table kb_chunks         enable row level security;
alter table eval_runs         enable row level security;
alter table eval_cases        enable row level security;
alter table prompt_registry   enable row level security;
alter table policy_registry   enable row level security;
alter table tickets           enable row level security;
alter table kb_meta           enable row level security;
alter table branches          enable row level security;
alter table service_slots     enable row level security;
alter table parts_orders      enable row level security;

-- ============ messages_public view ============

-- Masked-content-only projection of messages. Default (security definer)
-- semantics are intentional: the view owner bypasses base-table RLS, while
-- anon is never granted the base table — so raw `content` stays unreachable.
create view messages_public as
    select id, conversation_id, sender, content_masked, created_at
    from messages;

-- anon must never read raw message content: revoke base table entirely.
revoke all on messages from anon;
grant select on messages_public to anon, authenticated;

-- ============ anon policies ============

create policy anon_select_conversations on conversations
    for select to anon using (true);

create policy anon_select_branches on branches
    for select to anon using (true);

create policy anon_select_service_slots on service_slots
    for select to anon using (true);

-- No anon policy on customer_profiles, trace_events, tickets, eval_*,
-- *_registry, kb_chunks, kb_meta → RLS denies by default.

-- ============ authenticated (staff) policies ============

create policy staff_select_customer_profiles on customer_profiles
    for select to authenticated using (true);
create policy staff_select_conversations on conversations
    for select to authenticated using (true);
create policy staff_select_messages on messages
    for select to authenticated using (true);
create policy staff_select_trace_events on trace_events
    for select to authenticated using (true);
create policy staff_select_kb_chunks on kb_chunks
    for select to authenticated using (true);
create policy staff_select_eval_runs on eval_runs
    for select to authenticated using (true);
create policy staff_select_eval_cases on eval_cases
    for select to authenticated using (true);
create policy staff_select_prompt_registry on prompt_registry
    for select to authenticated using (true);
create policy staff_select_policy_registry on policy_registry
    for select to authenticated using (true);
create policy staff_select_tickets on tickets
    for select to authenticated using (true);
create policy staff_select_kb_meta on kb_meta
    for select to authenticated using (true);
create policy staff_select_branches on branches
    for select to authenticated using (true);
create policy staff_select_service_slots on service_slots
    for select to authenticated using (true);
create policy staff_select_parts_orders on parts_orders
    for select to authenticated using (true);

-- Staff may flip conversation mode (live takeover) — column-restricted grant.
create policy staff_update_conversations on conversations
    for update to authenticated using (true) with check (true);
revoke update on conversations from authenticated;
grant update (mode) on conversations to authenticated;

-- Staff may update ticket status (claim/resolve) — column-restricted grant.
create policy staff_update_tickets on tickets
    for update to authenticated using (true) with check (true);
revoke update on tickets from authenticated;
grant update (status) on tickets to authenticated;

-- Staff may send messages in live chat.
create policy staff_insert_messages on messages
    for insert to authenticated with check (true);

-- ============ Realtime ============

alter publication supabase_realtime add table messages;
alter publication supabase_realtime add table conversations;
alter publication supabase_realtime add table tickets;
