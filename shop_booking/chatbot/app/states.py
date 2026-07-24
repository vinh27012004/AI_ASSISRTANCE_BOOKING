"""Định nghĩa state + điều kiện — DD §3.1/§3.2, chatbot-architecture.md §3.

Mỗi state khai báo: slot phải có để RỜI state, điều kiện VÀO. Thứ tự bám 12 bước UC-01.
Đây là code deterministic — không đụng LLM (test được không cần mock model).
"""

from __future__ import annotations

from app.session import Session

# --- Hằng state ---
GREETING = "GREETING"
SHOP = "SHOP"
DATE = "DATE"
PARTY_SIZE = "PARTY_SIZE"
COURSE = "COURSE"        # chọn course chính
ADDON = "ADDON"          # chọn add-on (bước RIÊNG sau course)
SLOT = "SLOT"
THERAPIST = "THERAPIST"
CONTACT = "CONTACT"
CONFIRM = "CONFIRM"
CREATE = "CREATE"        # POST /bookings
UPDATE = "UPDATE"        # PATCH /bookings/{code} — sửa lịch trong phiên (UC-02)
DONE = "DONE"
CANCELLED = "CANCELLED"  # đã hủy trong phiên (UC-03)
MODIFY = "MODIFY"        # menu "đổi gì" (chỉ render + nút, không nằm trong vòng hỏi)
END = "END"      # nhánh chặn (A5 PHONE_BLOCKED)
HUMAN = "HUMAN"  # handoff (phase sau — MVP chỉ nút gọi cửa hàng, Q9)

# Thứ tự hỏi (bước ③). Bỏ DURATION (course đã quyết thời lượng); tách COURSE ↔ ADDON.
# THERAPIST TRƯỚC SLOT: chỉ định người trước -> GET /slots lọc đúng giờ trống của người đó
# (khách chỉ thấy giờ họ thực sự rảnh). Nhóm ≥2 bỏ qua THERAPIST -> SLOT hiện mọi giờ.
# CREATE/UPDATE/DONE/CANCELLED/END/HUMAN không nằm trong vòng hỏi.
STATE_ORDER = [
    GREETING, SHOP, DATE, PARTY_SIZE,
    COURSE, ADDON, THERAPIST, SLOT, CONTACT, CONFIRM,
]

TERMINAL_STATES = {DONE, CANCELLED, END, HUMAN}

# State nào gọi shop_api khi vào (bước ④) — dùng ở run_state_action.
STATES_WITH_API = {SHOP, COURSE, ADDON, SLOT, THERAPIST, CONTACT, CREATE, UPDATE}


def entry_condition(state: str, session: Session) -> bool:
    """Điều kiện VÀO state."""
    if state == THERAPIST:
        return session.slots.party_size == 1          # BR-04: nhóm không được chỉ định
    if state == ADDON:
        return session.slots.course_id is not None     # add-on phải kèm course (BR-01)
    return True


def slots_satisfied(state: str, session: Session) -> bool:
    """Đã đủ slot để RỜI state chưa."""
    s = session.slots
    if state == GREETING:
        return session.turn_count >= 1
    if state == SHOP:
        return s.shop_id is not None
    if state == DATE:
        return s.date is not None
    if state == PARTY_SIZE:
        return s.party_size in (1, 2, 3)
    if state == COURSE:
        return s.course_id is not None
    if state == ADDON:
        return s.addons_decided                        # add-on tùy chọn -> cần chốt tường minh
    if state == SLOT:
        return s.slot is not None
    if state == THERAPIST:
        return s.therapist_decided
    if state == CONTACT:
        # Phải qua lookup (chặn NG — BR-06) mới được rời CONTACT, không chỉ "có phone/email".
        return bool(s.phone and s.email and s.contact_verified)
    if state == CONFIRM:
        return s.confirm == "yes"
    return True


def is_terminal(state: str) -> bool:
    return state in TERMINAL_STATES
