"""Test offline — không cần pytest/LLM/Redis/shop_api thật (mẹo test §9, DD Mục 6).

Chạy:  python tests/test_chatbot.py   (từ thư mục chatbot/)
Bước ③④⑤ là code -> assert state kế + tool được gọi; LLM ở ①⑥ để None (fake).
"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ngày trong tương lai để bộ lọc "bỏ giờ đã qua của HÔM NAY" không đụng tới slot cố định.
_FUTURE_DATE = (date.today() + timedelta(days=2)).isoformat()

from app import pii
from app import state_machine as sm
from app import states as S
from app.config import Settings
from app.orchestrator import Orchestrator
from app.session import InMemorySessionStore, Session, Slots
from app.shop_api_client import ShopApiError

_PASSED = 0


def check(cond, msg):
    global _PASSED
    assert cond, "FAIL: " + msg
    _PASSED += 1


# --------------------------------------------------------------------------- #
#  Stub shop_api                                                              #
# --------------------------------------------------------------------------- #
class StubApi:
    def __init__(self):
        self.created_body = None
        self.patched_body = None
        self.cancelled_with = None
        self.lookup_error = None
        self.create_error = None
        self.last_slots_kw = None
        self.calls = []

    def get_shops(self):
        self.calls.append("shops")
        return [{"id": 1, "name": "Shop A", "address": "1 Rd", "phone": "090-1111"}]

    def get_services(self, shop_id, date, party_size=None):
        self.calls.append("services")
        return {"courses": [{"id": 3, "name": "Toàn thân", "duration_min": 60, "price": 5000}],
                "addons": [{"id": 7, "name": "Foot", "duration_min": 15, "price": 1000,
                            "restricted_course_ids": []}],
                "reason": None}

    def get_slots(self, shop_id, **kw):
        self.calls.append("slots")
        self.last_slots_kw = kw
        return {"slots": ["14:00", "14:15", "15:00"]}

    def get_therapists(self, shop_id, date):
        self.calls.append("therapists")
        return {"therapists": [{"id": 5, "name": "Hana", "gender": "female"}]}

    def lookup_customer(self, phone):
        self.calls.append("lookup:" + phone)
        if self.lookup_error:
            raise self.lookup_error
        return {"member_type": "guest", "rank": None, "visit_count": 0}

    def create_booking(self, body):
        self.calls.append("create")
        if self.create_error:
            raise self.create_error
        self.created_body = body
        return {"booking_code": "20260723-S001-AB12", "status": "confirmed",
                "edit_token": "tok", "edit_token_expires_in": 120}

    def patch_booking(self, booking_code, body, edit_token=None):
        self.calls.append("patch:" + booking_code)
        self.patched_body = body
        return {"booking_code": booking_code, "status": "confirmed"}

    def cancel_booking(self, booking_code, email):
        self.calls.append("cancel:" + booking_code)
        self.cancelled_with = email
        return {"booking_code": booking_code, "status": "cancelled"}


def _settings():
    return Settings(
        shop_api_base_url="http://x/api/v1",
        llm_base_url="", llm_api_key="", llm_model="m",
        redis_url="", session_ttl_seconds=1800, vault_enc_key="",
        fallback_shop_phone="090-9999",
    )


def _orch(api):
    return Orchestrator(InMemorySessionStore(), api, None, _settings())


def _drive(orch, cid, *messages):
    reply = None
    for m in messages:
        reply = orch.handle_turn(cid, m)
    return reply


# --------------------------------------------------------------------------- #
#  State machine (không LLM)                                                   #
# --------------------------------------------------------------------------- #
def test_t1_noi_gop():
    """T1: nói gộp date+party -> nhảy thẳng COURSE, không hỏi lại từng câu."""
    ses = Session(conversation_id="c", turn_count=1)
    sm.merge_params(ses, {"date": "2026-07-23", "party_size": 2})
    ses.slots.shop_id = 1  # đã có shop
    check(sm.next_state(ses) == S.COURSE, "T1 next_state phải là COURSE (đã bỏ DURATION)")


def test_t2_br04_party_change():
    """T2: party 1->3 xóa therapist (BR-04) và bỏ qua state THERAPIST."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="2026-07-23", party_size=1, duration=60,
                              course_id=3, slot="14:00", therapist_id=5, therapist_decided=True))
    sm.merge_params(ses, {"party_size": 3})
    check(ses.slots.therapist_id is None, "T2 therapist_id phải bị xóa")
    check(ses.slots.therapist_decided is False, "T2 therapist_decided phải reset")
    check(S.entry_condition(S.THERAPIST, ses) is False, "T2 không được vào THERAPIST khi nhóm 3")


def test_t3_party_over():
    """T3: >3 người -> party_over (nhánh handoff A8/BR-14)."""
    ses = Session(conversation_id="c", turn_count=1)
    sm.merge_params(ses, {"party_size": 5})
    check(ses.slots.party_over is True, "T3 party_over phải True")
    check(ses.slots.party_size is None, "T3 không set party_size khi >3")


def test_invalidate_on_course_change():
    """Đổi course (nút) -> xóa add-on + slot + confirm (add-on phụ thuộc course, BR-09)."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, course_id=3,
                              addons=[7], addons_decided=True, slot="14:00", confirm="yes"))
    sm.apply_button(ses, "course:9")
    check(ses.slots.course_id == 9, "course đổi sang 9")
    check(ses.slots.addons == [] and ses.slots.addons_decided is False, "đổi course phải reset add-on")
    check(ses.slots.slot is None, "đổi course phải xóa slot (BR-07)")
    check(ses.slots.confirm is None, "đổi đơn phải xóa confirm")


def test_addon_is_separate_step():
    """Chọn course KHÔNG tự nhảy qua SLOT — phải qua bước ADDON (chốt add-on) trước."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1))
    sm.apply_button(ses, "course:3")
    check(sm.next_state(ses) == S.ADDON, "sau course phải vào ADDON, chưa qua SLOT")
    sm.apply_button(ses, "addon:none")  # không thêm add-on
    check(sm.next_state(ses) == S.THERAPIST, "chốt add-on -> THERAPIST (party 1) trước SLOT")
    sm.apply_button(ses, "therapist:skip")
    check(sm.next_state(ses) == S.SLOT, "chọn người xong mới tới SLOT")


def test_therapist_before_slot_filters():
    """Chỉ định nhân viên TRƯỚC -> SLOT gọi GET /slots lọc theo đúng người đó."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "c7", "", "shop:1", f"date:{_FUTURE_DATE}", "party:1", "course:3", "addon:none")
    check(r.state == S.THERAPIST, f"phải hỏi nhân viên trước khi chọn giờ, đang {r.state}")
    r = orch.handle_turn("c7", "therapist:5")           # chỉ định nhân viên id=5
    check(r.state == S.SLOT, "chọn người xong mới tới SLOT")
    check(api.last_slots_kw.get("therapist_id") == 5, "SLOT phải lọc giờ theo nhân viên đã chọn")


# --------------------------------------------------------------------------- #
#  PII                                                                         #
# --------------------------------------------------------------------------- #
def test_match_therapist_by_name():
    """Khách nêu tên 'Hana' -> map về therapist_id, không hỏi lại (bug user báo)."""
    from app.orchestrator import Orchestrator
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, therapist_text="Hana"))
    ok = Orchestrator._match_therapist(ses, [{"id": 5, "name": "Hana", "gender": "female"}])
    check(ok is True, "phải khớp tên Hana")
    check(ses.slots.therapist_id == 5, "map đúng therapist_id")
    check(ses.slots.therapist_decided is True, "đã chỉ định -> không hỏi lại")


def test_future_slots_filters_past():
    """Đặt HÔM NAY -> bỏ giờ đã qua; ngày khác -> giữ nguyên."""
    from datetime import date, datetime, timedelta
    from app.orchestrator import Orchestrator
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    check(Orchestrator._future_slots(["08:00", "23:00"], tomorrow) == ["08:00", "23:00"],
          "ngày khác: không lọc")
    now = datetime.now()
    now_min = now.hour * 60 + now.minute
    if 90 <= now_min <= 24 * 60 - 90:                    # tránh mép nửa đêm cho ổn định
        past = (now - timedelta(minutes=60)).strftime("%H:%M")
        future = (now + timedelta(minutes=60)).strftime("%H:%M")
        res = Orchestrator._future_slots([past, future], date.today().isoformat())
        check(future in res and past not in res, "hôm nay: bỏ giờ đã qua, giữ giờ tương lai")


def test_order_slots_keeps_last_and_full_range():
    """Chưa nêu giờ -> hiện HẾT (kể cả 18:00, khớp FE); nêu giờ -> lấy các giờ gần nhất."""
    from app.orchestrator import Orchestrator
    full = [f"{h:02d}:{m:02d}" for h in range(10, 18) for m in (0, 15, 30, 45)] + ["18:00"]
    out = Orchestrator._order_slots(full, None)
    check(out == full, "không có giờ mong muốn -> hiện đầy đủ, không cắt đuôi (18:00 phải còn)")
    near = Orchestrator._order_slots(full, "15:00", limit=6)
    check(near[0] == "14:15" and near[-1] == "15:30" and len(near) == 6,
          "có giờ mong muốn -> 6 giờ gần 15:00, theo thứ tự thời gian")


def test_language_selection_and_lock():
    """Mở chat -> nút chọn ngôn ngữ; chọn xong -> hỏi tiếp bằng ngôn ngữ đó, KHÔNG tự đoán lại."""
    api = StubApi()
    orch = _orch(api)
    r = orch.handle_turn("cl", "")                          # mở chat
    vals = [b["value"] for b in r.ui["buttons"]]
    check(set(vals) == {"lang:vi", "lang:en", "lang:ja"}, "màn chào phải có 3 nút ngôn ngữ")

    r = orch.handle_turn("cl", "lang:en")                   # chọn English
    check(r.state == S.SHOP, "chọn ngôn ngữ xong -> hỏi cửa hàng")
    check("shop" in r.reply_text.lower(), "câu hỏi cửa hàng bằng tiếng Anh (fake template en)")

    # Đã khoá 'en': gõ câu tiếng Việt cũng KHÔNG lật ngôn ngữ.
    orch.handle_turn("cl", "shop:1")
    orch.handle_turn("cl", "cho tôi đặt lịch ngày mai với")  # có dấu tiếng Việt
    from app.session import InMemorySessionStore  # noqa: F401 — chỉ để rõ store
    ses = orch.store.load("cl")
    check(ses.lang == "en" and ses.lang_locked is True, "đã chọn en -> giữ en dù gõ tiếng Việt")


def test_detect_lang_ignores_pii():
    """Gõ SĐT + email KHÔNG được coi là tiếng Anh (bug bot nhảy sang EN ở CONTACT)."""
    from app import nlu
    check(nlu.detect_lang("0123456789 abc@gmail.com") is None,
          "SĐT + email -> không đủ tín hiệu ngôn ngữ (giữ nguyên tiếng đang dùng)")
    check(nlu.detect_lang("cho tôi đặt lịch nhé") == "vi", "câu có dấu -> vi")
    check(nlu.detect_lang("I want to book a massage") == "en", "câu tiếng Anh thật -> en")


def test_contact_asks_only_missing():
    """Đã cho số điện thoại -> chỉ hỏi email, không hỏi lại cả hai."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    p = nlg.build_prompt("CONTACT", Ses(conversation_id="c", lang="vi", slots=Sl(phone="{{phone_1}}")), {}, "vi")
    check(p["facts"]["hoi"] == "email", "đã có SĐT -> chỉ hỏi email")
    p2 = nlg.build_prompt("CONTACT", Ses(conversation_id="c", lang="vi"), {}, "vi")
    check("số điện thoại" in p2["facts"]["hoi"] and "email" in p2["facts"]["hoi"],
          "chưa có gì -> hỏi cả số điện thoại và email")


def test_t12_pii_mask():
    vault = {}
    masked = pii.mask("SĐT 0901234567, email a@b.com", vault)
    check("{{phone_1}}" in masked and "{{email_1}}" in masked, "T12 phải che phone+email")
    check("0901234567" not in masked, "T12 số thật không được lọt ra text LLM")
    check(vault["{{phone_1}}"] == "0901234567", "T12 vault giữ số thật")
    check(pii.unmask(masked, vault) == "SĐT 0901234567, email a@b.com", "T12 unmask khôi phục")
    # mask_response strip tên khách
    cleaned = pii.mask_response({"customer": {"name": "Nguyen", "member_type": "member"}})
    check("name" not in cleaned["customer"], "T12 mask_response phải bỏ tên khách")
    check(cleaned["customer"]["member_type"] == "member", "T12 giữ member_type")


def test_t13_pii_code():
    vault = {}
    masked = pii.mask("mã của tôi 20260723-S001-AB12 nhé", vault)
    check("{{code_1}}" in masked, "T13 phải che mã đặt chỗ")
    check(vault["{{code_1}}"] == "20260723-S001-AB12", "T13 vault giữ mã thật")


# --------------------------------------------------------------------------- #
#  Luồng đầy đủ qua Orchestrator (LLM=None, StubApi)                           #
# --------------------------------------------------------------------------- #
_HAPPY = ("", "shop:1", f"date:{_FUTURE_DATE}", "party:1",
          "course:3", "addon:none", "therapist:skip", "slot:14:00",
          "0901234567 a@b.com", "confirm:yes")


def test_happy_path():
    api = StubApi()
    orch = _orch(api)
    reply = _drive(orch, "c1", *_HAPPY)
    check(reply.state == S.DONE, f"happy: state phải DONE, đang {reply.state}")
    check(reply.done is True, "happy: done phải True")
    check("20260723-S001-AB12" in reply.reply_text, "happy: câu DONE phải có mã")
    check(api.created_body["phone"] == "0901234567", "happy: body gửi SĐT THẬT (đã unmask)")
    check(api.created_body["party_size"] == 1 and api.created_body["course_id"] == 3,
          "happy: body đúng party_size/course")
    check(api.created_body["start_time"] == "14:00", "happy: body đúng giờ")


def test_a5_phone_blocked():
    api = StubApi()
    api.lookup_error = ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                                    {"reason": "abc", "shop_phone": "090-1111"})
    orch = _orch(api)
    reply = _drive(orch, "c2",
                   "", "shop:1", f"date:{_FUTURE_DATE}", "party:1",
                   "course:3", "addon:none", "therapist:skip", "slot:14:00", "0901234567 a@b.com")
    check(reply.state == S.END, f"A5: state phải END, đang {reply.state}")
    check(api.created_body is None, "A5: KHÔNG được tạo booking")
    check("090-1111" in reply.reply_text, "A5: phải đưa số cửa hàng")


def test_a6_slot_conflict():
    api = StubApi()
    api.create_error = ShopApiError(409, "SLOT_CONFLICT", "Giờ vừa hết.",
                                    {"suggested_slots": ["14:30", "15:15"]})
    orch = _orch(api)
    reply = _drive(orch, "c3", *_HAPPY)
    check(reply.state == S.SLOT, f"A6: quay lại SLOT, đang {reply.state}")
    values = [b["value"] for b in reply.ui["buttons"]]
    check("slot:14:30" in values and "slot:15:15" in values, "A6: hiện suggested_slots làm nút")


def test_handoff_button():
    api = StubApi()
    orch = _orch(api)
    reply = _drive(orch, "c4", "", "shop:1", "cho tôi gặp nhân viên")
    values = [b["value"] for b in reply.ui["buttons"]]
    check("handoff:call" in values, "handoff: phải có nút gọi cửa hàng")


def test_modify_slot_in_session():
    """Sau khi đặt xong, sửa giờ trong phiên -> PATCH với giờ mới (UC-02, BR-17)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c5", *_HAPPY)                       # đặt xong -> DONE
    menu = orch.handle_turn("c5", "modify:start")
    check(menu.state == S.DONE and any(b["value"] == "modify:slot" for b in menu.ui["buttons"]),
          "modify: nút Sửa lịch hiện menu đổi gì")
    orch.handle_turn("c5", "modify:slot")             # -> quay lại SLOT
    orch.handle_turn("c5", "slot:14:15")              # chọn giờ mới -> CONFIRM
    reply = orch.handle_turn("c5", "confirm:yes")     # đồng ý -> PATCH
    check(api.patched_body is not None, "modify: phải gọi PATCH")
    check(api.patched_body["start_time"] == "14:15", "modify: PATCH đúng giờ mới")
    check(reply.state == S.DONE, "modify: xong quay lại DONE")


def test_cancel_in_session():
    """Sau khi đặt xong, hủy trong phiên -> cancel với email thật (UC-03)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c6", *_HAPPY)
    reply = orch.handle_turn("c6", "cancel:start")
    check(api.cancelled_with == "a@b.com", "cancel: gửi email THẬT (đã unmask)")
    check(reply.state == S.CANCELLED and reply.done is True, "cancel: state CANCELLED, done")


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{_PASSED} checks passed across {len(tests)} tests.")


if __name__ == "__main__":
    run_all()
