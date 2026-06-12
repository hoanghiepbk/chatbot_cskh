-- TIP-006 Migration 0005: capacity CHECK + atomic slot booking RPCs.
-- The CHECK is the last line of defense; the RPCs make book/release atomic so
-- two concurrent bookings can never oversell a slot (single UPDATE, row lock).

alter table service_slots
    add constraint booked_within_capacity check (booked <= capacity);

-- 0 rows returned (null) = slot already full — caller maps to SLOT_FULL.
create or replace function book_slot_atomic(p_slot_id uuid)
returns service_slots
language sql
security definer
set search_path = public
as $$
    update service_slots
    set booked = booked + 1
    where id = p_slot_id and booked < capacity
    returning *;
$$;

create or replace function release_slot_atomic(p_slot_id uuid)
returns service_slots
language sql
security definer
set search_path = public
as $$
    update service_slots
    set booked = greatest(booked - 1, 0)
    where id = p_slot_id
    returning *;
$$;

revoke execute on function book_slot_atomic(uuid) from public, anon, authenticated;
grant execute on function book_slot_atomic(uuid) to service_role;
revoke execute on function release_slot_atomic(uuid) from public, anon, authenticated;
grant execute on function release_slot_atomic(uuid) to service_role;
