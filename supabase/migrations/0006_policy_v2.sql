-- TIP-008 Migration 0006: policy_registry v2 (v1 rules + injection_threshold)
-- Moves the injection threshold out of the TIP-005 hardcode into policy data.

update policy_registry set active = false where name = 'core_policy' and version = 1;

insert into policy_registry (name, version, rules, active) values
    ('core_policy', 2,
     '{"refund_cap_vnd":2000000,"write_value_cap_vnd":5000000,"escalate_confidence_below":0.7,"injection_threshold":0.5,"forbidden_topics":["tư vấn pháp lý","so sánh đối thủ"]}',
     true);
