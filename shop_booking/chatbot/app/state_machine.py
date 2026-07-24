"""State machine (bước ②③) — DD §3.2/§3.3. Code thuần, không LLM.

- next_state: state đầu tiên (theo STATE_ORDER) còn thiếu slot, bỏ qua state không đủ
  điều kiện vào (vd THERAPIST khi nhóm >1 — BR-04).
- merge_params: gộp entity NLU đã trích vào slots + XÓA slot mâu thuẫn (BR-04/BR-07).
- apply_button: token "key:value" từ nút bấm -> slots (tất định, KHÔNG qua LLU/LLM,
  giảm NLU sai — §7/§10).
"""

from __future__ import annotations

from app.session import Session
from app import states as S

KNOWN_BUTTON_KEYS = {
    "shop", "date", "party", "duration", "course",
    "addon", "slot", "therapist", "confirm", "handoff",
    "modify", "cancel", "lang",
}


def next_state(session: Session) -> str:
    """Bước ③ — chọn state kế. Đủ hết tới CONFIRM: đang sửa -> UPDATE (PATCH), chưa đặt ->
    CREATE (POST), đã đặt xong -> DONE."""
    for st in S.STATE_ORDER:
        if not S.entry_condition(st, session):
            continue
        if not S.slots_satisfied(st, session):
            return st
    if session.editing:
        return S.UPDATE
    return S.DONE if session.booking_code else S.CREATE


# --------------------------------------------------------------------------- #
#  Merge NLU entities                                                          #
# --------------------------------------------------------------------------- #

def merge_params(session: Session, entities: dict) -> None:
    """Bước ② — gộp entity (chỉ field không null) vào slots, rồi vô hiệu hóa slot mâu thuẫn."""
    s = session.slots
    changed: set[str] = set()

    def _set(field: str, value) -> None:
        if getattr(s, field) != value:
            setattr(s, field, value)
            changed.add(field)

    date = entities.get("date")
    if date:
        _set("date", date)

    time = entities.get("time")
    if time:
        s.wanted_time = time  # "giờ mong muốn" — không tính là đổi slot

    ps = entities.get("party_size")
    if ps is not None:
        try:
            ps = int(ps)
        except (TypeError, ValueError):
            ps = None
        if ps is not None:
            if ps > 3:
                s.party_over = True           # BR-14 -> nhánh handoff, không set party_size
            elif 1 <= ps <= 3:
                s.party_over = False
                _set("party_size", ps)

    dur = entities.get("duration")
    if dur is not None:
        try:
            _set("duration", int(dur))
        except (TypeError, ValueError):
            pass

    course = entities.get("course")
    if course:
        s.course_text = str(course)          # gợi ý — map id qua GET /services (DD §2.3)
        if s.course_id is not None:          # khách đổi course giữa chừng -> map lại từ đầu
            s.course_id = None
            changed.add("course_id")

    ther = entities.get("therapist")
    if ther:
        t = str(ther).lower()
        if t in ("none", "skip", "khong", "không", "no"):
            s.therapist_id = None
            s.therapist_gender = None
            s.therapist_decided = True
            s.slot = None; s.confirm = None      # đổi lựa chọn người -> chọn lại giờ
        elif t in ("male", "nam", "female", "nu", "nữ"):
            gender = "male" if t in ("male", "nam") else "female"
            if s.party_size == 1:            # chỉ 1 người mới được chỉ định (BR-04)
                s.therapist_gender = gender
                s.therapist_id = None
                s.therapist_decided = True
                s.slot = None; s.confirm = None
        else:
            s.therapist_text = str(ther)     # tên -> map id qua GET /therapists

    confirm = entities.get("confirm")
    if confirm in ("yes", "no"):
        s.confirm = confirm

    _invalidate(session, changed)


def _invalidate(session: Session, changed: set[str]) -> None:
    """XÓA slot không còn chắc hợp lệ sau khi đổi điều kiện (§3.3)."""
    s = session.slots
    # BR-04: nhóm >1 không được chỉ định therapist.
    if "party_size" in changed and (s.party_size or 0) > 1:
        s.therapist_id = None
        s.therapist_gender = None
        s.therapist_decided = False
    # Đổi course -> add-on cũ chưa chắc còn kèm được (BR-09), buộc chọn lại add-on.
    if "course_id" in changed:
        s.addons = []
        s.addons_decided = False
        s.course_name = None
    # BR-07: đổi course/party/date -> slot cũ chưa chắc còn hợp lệ, buộc chọn lại.
    if changed & {"course_id", "party_size", "date"}:
        s.slot = None
        s.confirm = None                     # đổi đơn thì phải xác nhận lại


# --------------------------------------------------------------------------- #
#  Button tokens (nút bấm)                                                     #
# --------------------------------------------------------------------------- #

def is_button_token(text: str) -> bool:
    t = (text or "").strip()
    if ":" not in t:
        return False
    key = t.split(":", 1)[0]
    return " " not in key and key in KNOWN_BUTTON_KEYS


def apply_button(session: Session, text: str) -> str | None:
    """Áp token nút vào slots. Trả tín hiệu cho orchestrator ('handoff'/'modify_menu'/
    'cancel') hoặc None nếu token thường."""
    s = session.slots
    key, _, value = text.strip().partition(":")
    changed: set[str] = set()

    if key == "handoff":
        return "handoff"

    if key == "lang":                            # khách chọn ngôn ngữ ở màn chào
        if value in ("vi", "en", "ja"):
            session.lang = value
            session.lang_locked = True           # đã chọn -> ngừng tự đoán (§7)
        return None

    # --- Sửa/hủy lịch đã đặt (UC-02/03) ---
    if key == "modify":
        if value == "start":
            session.editing = True
            return "modify_menu"
        if value == "keep":                          # thôi, giữ nguyên -> quay lại DONE
            session.editing = False
            return None
        session.editing = True
        if value == "slot":
            s.slot = None; s.confirm = None
        elif value == "party":
            s.party_size = None; s.slot = None; s.confirm = None
            s.therapist_id = None; s.therapist_gender = None; s.therapist_decided = False
        elif value == "course":
            s.course_id = None; s.course_name = None
            s.addons = []; s.addons_decided = False
            s.slot = None; s.confirm = None
        return None
    if key == "cancel" and value == "start":
        return "cancel"

    if key == "shop":
        s.shop_id = _to_int(value); changed.add("shop_id")
    elif key == "date":
        s.date = value; changed.add("date")
    elif key == "party":
        n = _to_int(value)
        if n and n > 3:
            s.party_over = True
        elif n:
            s.party_over = False
            s.party_size = n; changed.add("party_size")
    elif key == "course":
        s.course_id = _to_int(value); changed.add("course_id")
    elif key == "addon":
        if value == "done":                          # chốt add-on đã chọn
            s.addons_decided = True
        elif value in ("none", "skip"):              # không thêm add-on
            s.addons = []; s.addons_decided = True
        else:
            aid = _to_int(value)
            if aid is not None:
                if aid in s.addons:
                    s.addons.remove(aid)
                else:
                    s.addons.append(aid)
    elif key == "slot":
        s.slot = value; changed.add("slot")
    elif key == "therapist":
        if value == "skip":
            s.therapist_id = None; s.therapist_gender = None
        elif value in ("male", "female"):
            s.therapist_gender = value; s.therapist_id = None
        else:
            s.therapist_id = _to_int(value); s.therapist_gender = None
        s.therapist_decided = True
        # Giờ trống phụ thuộc người phục vụ -> đổi người thì chọn lại giờ.
        s.slot = None; s.confirm = None
    elif key == "confirm":
        s.confirm = "yes" if value == "yes" else "no"

    _invalidate(session, changed)
    return None


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
