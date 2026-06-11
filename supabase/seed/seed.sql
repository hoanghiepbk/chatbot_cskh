-- TIP-002 seed: demo data for the 5 business flows (no real PII — demo phones only)

-- ============ branches (3) ============

insert into branches (id, name, address, district, open_hours) values
    ('b0000000-0000-4000-8000-000000000001', 'XeCare Thanh Xuân',
     '123 Nguyễn Trãi', 'Thanh Xuân',
     '{"mon_sat":"08:00-18:00","sun":"08:00-12:00"}'),
    ('b0000000-0000-4000-8000-000000000002', 'XeCare Cầu Giấy',
     '45 Trần Thái Tông', 'Cầu Giấy',
     '{"mon_sat":"08:00-18:00","sun":"08:00-12:00"}'),
    ('b0000000-0000-4000-8000-000000000003', 'XeCare Hà Đông',
     '78 Quang Trung', 'Hà Đông',
     '{"mon_sat":"08:00-18:00","sun":"08:00-12:00"}');

-- ============ service_slots (7 days × 3 branches × 6 slots = 126) ============
-- Motorbike slots: 08:00, 10:00, 14:00, 16:00. Car slots: 09:00, 15:00.
-- Every 5th slot is pre-filled (booked = capacity) so the "suggest nearest free
-- slot" flow has real data to filter — 25/126 ≈ 20%.

with slot_grid as (
    select
        b.id as branch_id,
        (current_date + d.day_offset)::timestamptz + s.slot_time as starts_at,
        s.vehicle_type,
        row_number() over (order by d.day_offset, b.id, s.slot_time) as rn
    from branches b
    cross join generate_series(1, 7) as d(day_offset)
    cross join (values
        (interval '8 hours',  'motorbike'),
        (interval '10 hours', 'motorbike'),
        (interval '14 hours', 'motorbike'),
        (interval '16 hours', 'motorbike'),
        (interval '9 hours',  'car'),
        (interval '15 hours', 'car')
    ) as s(slot_time, vehicle_type)
)
insert into service_slots (branch_id, starts_at, vehicle_type, capacity, booked)
select branch_id, starts_at, vehicle_type, 1,
       case when rn % 5 = 0 then 1 else 0 end
from slot_grid;

-- ============ customer_profiles (4) ============
-- phone_hash = sha256('DEMO_SALT' || phone_E164), precomputed:
--   sha256('DEMO_SALT+84901000001') = 39046450e23e02b0b616719c2743418445bf4d6260a8ad27c5da4b9b48b1b880
--   sha256('DEMO_SALT+84901000002') = 42db787e4ad7e31539cbea8ead61a20334e02a17bb80872d40fd8d2d1da40077
--   sha256('DEMO_SALT+84901000003') = 6a2bdcbb2b2912759f5ec9aa6de44767623a1906cc04fe1dc43c8219182a6660
--   sha256('DEMO_SALT+84901000004') = 0129f3ea7b3c13117eb12a99adcd08c5ae26c5bb2b7442dfb46bd03dfac79007

insert into customer_profiles (id, phone_hash, display_name, vehicles, facts) values
    ('c0000000-0000-4000-8000-000000000001',
     '39046450e23e02b0b616719c2743418445bf4d6260a8ad27c5da4b9b48b1b880',
     'Anh Tuấn',
     '[{"type":"motorbike","model":"Honda Winner X","year":2023,"last_km":19500,"last_service_at":"2026-03-15"}]',
     '{"prefers_branch":"Thanh Xuân"}'),
    ('c0000000-0000-4000-8000-000000000002',
     '42db787e4ad7e31539cbea8ead61a20334e02a17bb80872d40fd8d2d1da40077',
     'Chị Hằng',
     '[{"type":"motorbike","model":"Honda SH","year":2021,"last_km":31200,"last_service_at":"2026-01-20"}]',
     '{"prefers_weekend":true,"complained_before":false}'),
    ('c0000000-0000-4000-8000-000000000003',
     '6a2bdcbb2b2912759f5ec9aa6de44767623a1906cc04fe1dc43c8219182a6660',
     'Anh Minh',
     '[{"type":"car","model":"VinFast Lux A","year":2022,"last_km":42800,"last_service_at":"2026-04-02"},
       {"type":"motorbike","model":"Honda Air Blade","year":2020,"last_km":27600,"last_service_at":"2025-11-10"}]',
     '{"prefers_branch":"Cầu Giấy","fleet_customer":true}'),
    ('c0000000-0000-4000-8000-000000000004',
     '0129f3ea7b3c13117eb12a99adcd08c5ae26c5bb2b7442dfb46bd03dfac79007',
     'Chị Linh',
     '[{"type":"motorbike","model":"Honda Vision","year":2024,"last_km":4100,"last_service_at":null}]',
     '{"new_customer":true}');

-- ============ parts_orders (5, mixed statuses, ≥1 paid) ============
-- The paid=true order backs the "cancel a paid order → escalate only" rule test.

insert into parts_orders (customer_id, items, status, total_vnd, paid) values
    ('c0000000-0000-4000-8000-000000000001',
     '[{"sku":"NHOT-CASTROL-10W40","name":"Nhớt Castrol 10W40","qty":2,"price_vnd":180000}]',
     'processing', 360000, false),
    ('c0000000-0000-4000-8000-000000000002',
     '[{"sku":"LOP-MICHELIN-SH","name":"Lốp Michelin City Grip cho SH","qty":1,"price_vnd":1250000}]',
     'shipped', 1250000, true),
    ('c0000000-0000-4000-8000-000000000003',
     '[{"sku":"ACQUY-LUXA","name":"Ắc quy VinFast Lux A","qty":1,"price_vnd":2800000},
       {"sku":"GAT-MUA-LUXA","name":"Gạt mưa Bosch","qty":2,"price_vnd":350000}]',
     'delivered', 3500000, true),
    ('c0000000-0000-4000-8000-000000000004',
     '[{"sku":"DAU-PHANH-DOT4","name":"Dầu phanh DOT4","qty":1,"price_vnd":95000}]',
     'cancelled', 95000, false),
    ('c0000000-0000-4000-8000-000000000001',
     '[{"sku":"MA-PHANH-WINNER","name":"Má phanh Winner X","qty":1,"price_vnd":420000}]',
     'processing', 420000, false);

-- ============ policy_registry v1 ============

insert into policy_registry (name, version, rules, active) values
    ('core_policy', 1,
     '{"refund_cap_vnd":2000000,"write_value_cap_vnd":5000000,"escalate_confidence_below":0.7,"forbidden_topics":["tư vấn pháp lý","so sánh đối thủ"]}',
     true);

-- ============ prompt_registry v1 ============
-- v1 inactive: migration 0004 (TIP-005) ships system_main v2 active=true, and
-- migrations run BEFORE seed on db reset — seeding v1 active would create two
-- active rows.

insert into prompt_registry (name, version, content, active) values
    ('system_main', 1, 'PLACEHOLDER — TIP-005 will provide the real system prompt.', false);
