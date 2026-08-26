"""State machine (bước ②③) — DD §3.2/§3.3. Code thuần, không LLM.

- next_state: state đầu tiên (theo STATE_ORDER) còn thiếu slot, bỏ qua state không đủ
  điều kiện vào (vd THERAPIST khi nhóm >1 — BR-04).
- merge_params: gộp entity NLU đã trích vào slots + XÓA slot mâu thuẫn (BR-04/BR-07).

KHÔNG còn nút bấm: mọi lựa chọn đến từ lời khách qua NLU. Các `*_text` là gợi ý thô, phải
map về id ở orchestrator (._match_*) mới dùng được.
"""

from __future__ import annotations

from app.session import Session
from app import matching
from app import states as S


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

    shop = entities.get("shop")
    # NHẮC LẠI cửa hàng đang chọn không phải là đổi cửa hàng. Bug thật trong log: khách nói
    # "Tôi chọn Hải Châu lúc 7h tối với dịch vụ tôi đã chọn" — Hải Châu đúng là shop đang chọn,
    # vậy mà gói + add-on bị xoá sạch rồi bot hỏi lại từ đầu, ngược hẳn ý "dịch vụ đã chọn".
    if (shop and s.shop_id is not None and s.shop_name
            and matching.name_matches(str(shop), s.shop_name)):
        shop = None
    if shop:
        s.shop_text = str(shop)         # map id qua GET /shops (orchestrator._match_shop)
        if s.shop_id is not None:
            # Đổi shop GIỮA CHỪNG: course/add-on/nhân viên/giờ đều thuộc về shop cũ (id
            # không dùng lại được ở shop khác) -> bỏ hết. Giữ ngày, số người, liên hệ.
            s.shop_id = None
            s.shop_name = None
            changed.add("shop_id")

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

    # KHÔNG nhận `duration` từ NLU: thời lượng do COURSE quyết (orchestrator._cache_course
    # ghi từ duration_min của gói). NLU stateless hay bịa ra 60 khi khách chỉ nói "3 người",
    # thậm chí ghi đè 120 -> 60, làm bẩn đơn mà chẳng để làm gì.

    course = entities.get("course")
    if course:
        s.course_text = str(course)          # gợi ý — map id qua GET /services (DD §2.3)
        if s.course_id is not None:          # khách đổi course giữa chừng -> map lại từ đầu
            s.course_id = None
            changed.add("course_id")

    addons = entities.get("addons")
    if addons:
        # Tên add-on khách nói -> gợi ý thô, map id ở orchestrator._match_addons.
        s.addon_texts = [str(a) for a in addons if a]

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


def clear_shop(session: Session) -> None:
    """Khách đòi đổi cửa hàng -> bỏ shop và MỌI thứ thuộc catalog shop cũ (course/add-on/
    nhân viên/giờ đều mang id riêng của shop), quay lại bước chọn cửa hàng. Giữ ngày, số
    người và thông tin liên hệ vì chúng không phụ thuộc cửa hàng."""
    s = session.slots
    s.shop_id = None
    s.shop_name = None
    s.shop_text = None
    _invalidate(session, {"shop_id"})


def _invalidate(session: Session, changed: set[str]) -> None:
    """XÓA slot không còn chắc hợp lệ sau khi đổi điều kiện (§3.3)."""
    s = session.slots
    # Đổi cửa hàng -> mọi id thuộc catalog cửa hàng cũ đều vô nghĩa.
    if "shop_id" in changed:
        s.course_id = None
        s.course_name = None
        s.therapist_id = None
        s.therapist_gender = None
        s.therapist_decided = False
        s.slot = None
        s.confirm = None
        reset_addons(s)
    # BR-04: nhóm >1 không được chỉ định therapist.
    if "party_size" in changed and (s.party_size or 0) > 1:
        s.therapist_id = None
        s.therapist_gender = None
        s.therapist_decided = False
    # Ngược lại: hạ xuống 1 người thì bước THERAPIST mở ra. Nếu đơn ĐÃ đi sâu (qua được
    # lookup liên hệ) thì khách đang sửa chứ không đặt mới — bật ra một câu hỏi họ chưa
    # từng được hỏi là giật. Mặc định để cửa hàng sắp, khách vẫn nói được nếu muốn.
    if "party_size" in changed and s.party_size == 1 and s.contact_verified             and not s.therapist_decided:
        s.therapist_decided = True
    # Đổi course -> add-on cũ chưa chắc còn kèm được (BR-09) -> chọn lại.
    # KHÔNG reset khi đổi SỐ NGƯỜI: từ khi cả nhóm dùng chung một bộ add-on (BR-10, BA cập
    # nhật), add-on không còn phụ thuộc số người — bắt chọn lại là hỏi thừa.
    if "course_id" in changed:
        reset_addons(s)
    if "course_id" in changed:
        s.course_name = None
    # BR-07: đổi course/party/date -> slot cũ chưa chắc còn hợp lệ, buộc chọn lại.
    if changed & {"course_id", "party_size", "date"}:
        s.slot = None
        s.confirm = None                     # đổi đơn thì phải xác nhận lại


# --------------------------------------------------------------------------- #
#  Sửa lịch đã đặt (UC-02) — khách nói "đổi giờ"/"đổi số người"/…              #
# --------------------------------------------------------------------------- #

def apply_modify_target(session: Session, target: str) -> None:
    """Xóa slot tương ứng phần khách muốn đổi, để vòng hỏi quay lại đúng bước đó.
    `target` lấy từ nlu.detect_modify_target ('slot'|'party'|'course'|'keep')."""
    s = session.slots
    if target == "keep":                         # thôi, giữ nguyên -> quay lại DONE
        session.editing = False
        return

    session.editing = True
    if target == "slot":
        s.slot = None; s.confirm = None
    elif target == "party":
        # Add-on dùng chung nên KHÔNG phụ thuộc số người -> giữ nguyên. Nhóm ≥2 không được
        # chỉ định therapist (BR-04) nên bỏ chỉ định.
        s.party_size = None; s.slot = None; s.confirm = None
        s.therapist_id = None; s.therapist_gender = None; s.therapist_decided = False
    elif target == "course":
        # Đổi course -> add-on cũ chưa chắc còn kèm được (BR-09), chọn lại từ đầu.
        s.course_id = None; s.course_name = None
        reset_addons(s)
        s.slot = None; s.confirm = None


# --------------------------------------------------------------------------- #
#  Add-on dùng CHUNG cho cả nhóm (BR-10, BA cập nhật)                          #
# --------------------------------------------------------------------------- #

def skip_addons(session: Session) -> None:
    """Khách nói "không thêm gì" -> chốt luôn với danh sách rỗng."""
    s = session.slots
    s.addon_ids = []
    s.addon_names = []
    s.addons_decided = True


def reset_addons(s) -> None:
    """Xóa lựa chọn add-on -> hỏi lại. Dùng khi đổi course (BR-09: combo cấm có thể khác)
    hoặc đổi số người, kể cả lúc sửa lịch."""
    s.addon_ids = []
    s.addon_names = []
    s.addons_decided = False
