"""Hard emergency keyword lists for pre_gate (Blueprint §6.2 layer 1).

All terms in *_NORMALIZED lists are matched as substrings on lowercase,
diacritics-stripped text. DIACRITIC_TERMS are matched on lowercase text WITH
diacritics kept — for words whose stripped form collides with common words
(e.g. "cháy" (fire) vs "chạy" (run) both strip to "chay").
"""

# Accident / collision
ACCIDENT_TERMS = [
    "tai nan",
    "va cham",
    "dam xe",
    "dam vao",
    "nga xe",
]

# Brake / steering failure — life-critical
CONTROL_FAILURE_TERMS = [
    "mat phanh",
    "phanh mat",
    "phanh khong an",
    "mat lai",
]

# Breakdown in a dangerous location
STRANDED_TERMS = [
    "chet may giua duong",
    "chet may tren cao toc",
    "no lop khi chay",
    "ket giua duong",
]

# Fire / smoke — "boc khoi"/"boc chay" are unambiguous when stripped
FIRE_TERMS_NORMALIZED = [
    "boc khoi",
    "boc chay",
]
# "cháy" must keep its diacritic to not collide with "chạy" (run)
FIRE_TERMS_DIACRITIC = [
    "cháy",
]

# Danger to people
DANGER_TERMS = [
    "nguy hiem den nguoi",
]

TERMS_NORMALIZED = (
    ACCIDENT_TERMS + CONTROL_FAILURE_TERMS + STRANDED_TERMS + FIRE_TERMS_NORMALIZED + DANGER_TERMS
)
TERMS_DIACRITIC = FIRE_TERMS_DIACRITIC

# "trên cao tốc" alone is not an emergency — only combined with one of these
# signals in the same message (e.g. "xe hỏng trên cao tốc").
HIGHWAY_CONTEXT = "tren cao toc"
HIGHWAY_SIGNALS = ["hong", "chet may", "no lop", "het xang", "khong no may"]

# "cứu hộ" only triggers when paired with an urgency signal — "đặt lịch cứu hộ
# tuần sau" or "hỏi phí cứu hộ" must NOT trigger (matched as whole words).
RESCUE_CONTEXT = "cuu ho"
RESCUE_URGENCY_WORDS = ["gap", "khan", "ngay", "dang"]
