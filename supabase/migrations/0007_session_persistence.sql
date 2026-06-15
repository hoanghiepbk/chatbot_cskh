-- TIP-008b Migration 0007: persist per-conversation session in a jsonb column.
-- Holds: pii (placeholder↔value map), action (slots+pending_action),
-- emergency (open+asks), hitl (complaint_attempted+handback_note).
-- The pii map is raw PII → this column is SERVICE-ROLE ONLY.

alter table conversations add column session jsonb not null default '{}'::jsonb;

-- SECURITY FIX (TIP-008b): TIP-002 granted anon/authenticated SELECT on
-- conversations (using(true)) for widget realtime mode-flips. That would now
-- expose the PII map in `session`. Re-confirm + tighten: keep row policies, but
-- revoke table-wide column read and re-grant every column EXCEPT session.
-- service_role bypasses RLS and keeps full access (the agent reads/writes session).
revoke select on conversations from anon;
revoke select on conversations from authenticated;
grant select (id, customer_id, mode, channel, started_at, closed_at, resolution)
    on conversations to anon;
grant select (id, customer_id, mode, channel, started_at, closed_at, resolution)
    on conversations to authenticated;
