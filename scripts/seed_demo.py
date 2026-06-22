"""TIP-016 — seed demo conversations through the LIVE agent HTTP API.

Runs ~10 realistic conversations so the console (Trace Explorer, Ops, HITL Queue,
Insights) and the widget have data to show during VERIFY. It only speaks HTTP —
no DB credentials, no service_role — so it is safe to point at a production URL.

Prereqs: the agent service is running and reachable, Supabase is seeded
(customer_profiles for the demo phones), and the KB is ingested.

    # local
    cd agent && uv run python ../scripts/seed_demo.py
    # production (point at the Railway URL)
    uv run python ../scripts/seed_demo.py --base-url https://<agent>.up.railway.app --confirm

The demo phones match supabase/seed/seed.sql (Anh Tuấn / Chị Hằng / Anh Minh /
Chị Linh) so greetings carry the customer's vehicle.
"""

import argparse
import sys

import httpx

# Vietnamese labels are printed; force UTF-8 so a redirected Windows cp1252 console
# does not crash on diacritics (errors='replace' as a backstop).
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# These four phones are seeded in supabase/seed/seed.sql with known vehicles.
TUAN, HANG, MINH, LINH = (
    "+84901000001",
    "+84901000002",
    "+84901000003",
    "+84901000004",
)

# (label, phone, [turns], confirm_after) — one entry per demo conversation.
# Spread chosen so VERIFY can exercise every screen: faq+citation, cache repeat,
# booking→confirm, rescue→escalate ticket, complaint→escalate ticket, gap, chitchat.
FAQ_WINNER = "Xe Winner X của em chạy hơn 20000 km rồi thì cần bảo dưỡng những gì ạ?"

SCENARIOS = [
    ("faq · bảo dưỡng (citation)", TUAN, [FAQ_WINNER], False),
    # exact-repeat of the question above → should be served from the semantic cache
    ("faq · CACHE repeat", HANG, [FAQ_WINNER], False),
    ("faq · giá ước tính (citation)", MINH,
     ["Thay nhớt cho ô tô VinFast Lux A giá khoảng bao nhiêu ạ?"], False),
    ("faq · bảo hành (citation)", LINH,
     ["Cho em hỏi chính sách bảo hành của XeCare thế nào ạ?"], False),
    ("booking · tới confirm card", TUAN,
     ["Em muốn đặt lịch bảo dưỡng xe Winner X ở chi nhánh Thanh Xuân",
      "8 giờ sáng mai giúp em nhé"], True),
    ("rescue · cứu hộ khẩn cấp", MINH,
     ["Toi bi tai nan tren cao toc, xe khong di duoc, cho em xin cuu ho"], False),
    ("complaint · escalate ticket", HANG,
     ["Dịch vụ lần trước quá tệ, nhân viên làm xước xe của tôi mà không xin lỗi",
      "Tôi không chấp nhận, tôi muốn gặp người phụ trách ngay"], False),
    ("gap · ngoài KB", LINH,
     ["XeCare có dịch vụ cho thuê xe tự lái theo ngày không ạ?"], False),
    ("gap · ngoài KB 2", TUAN,
     ["Bên mình có lắp camera hành trình và dán phim cách nhiệt ô tô không?"], False),
    ("chitchat · chào hỏi", LINH, ["Xin chào XeCare ạ"], False),
]


def start(client: httpx.Client, base: str, phone: str) -> str:
    r = client.post(f"{base}/chat/start", json={"phone": phone})
    r.raise_for_status()
    return r.json()["conversation_id"]


def send(client: httpx.Client, base: str, cid: str, text: str) -> dict:
    r = client.post(f"{base}/chat/{cid}/message", json={"text": text})
    r.raise_for_status()
    return r.json()


def confirm(client: httpx.Client, base: str, cid: str) -> dict:
    r = client.post(f"{base}/chat/{cid}/confirm", json={"accept": True})
    r.raise_for_status()
    return r.json()


def _summary(resp: dict) -> str:
    if resp.get("mode") == "human":
        return "mode=human"
    bits = [f"intent={resp.get('intent')}"]
    if resp.get("escalated"):
        bits.append("ESCALATED")
    cites = resp.get("citations") or []
    if cites:
        bits.append(f"citations={len(cites)}")
    pending = resp.get("pending_action")
    if pending:
        bits.append(f"pending={pending.get('type')}")
    return " ".join(bits)


def run(base: str, do_confirm: bool) -> int:
    created = 0
    with httpx.Client(timeout=180) as client:
        for label, phone, turns, confirm_after in SCENARIOS:
            try:
                cid = start(client, base, phone)
                last = {}
                for turn in turns:
                    last = send(client, base, cid, turn)
                line = _summary(last)
                if confirm_after and do_confirm and (last.get("pending_action")):
                    c = confirm(client, base, cid)
                    line += f" → confirmed(executed={c.get('executed')})"
                print(f"  [OK] {label:32s} {cid[:8]}  {line}")
                created += 1
            except Exception as exc:  # one bad scenario must not abort the seed
                print(f"  [ERR] {label:32s} {exc!r}")
    print(f"\nSeeded {created}/{len(SCENARIOS)} conversations at {base}")
    print("Tip: run the eval runner too so the Eval Dashboard has data:")
    print("  cd agent && uv run python ../evals/runner.py --suite golden --limit 8")
    return 0 if created else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Seed demo conversations via the agent API")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--confirm", action="store_true",
                   help="accept the booking confirm card (executes a real book_slot write)")
    args = p.parse_args()
    return run(args.base_url.rstrip("/"), args.confirm)


if __name__ == "__main__":
    sys.exit(main())
