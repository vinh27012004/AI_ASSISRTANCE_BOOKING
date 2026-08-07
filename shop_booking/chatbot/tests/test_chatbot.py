"""Test offline — không cần pytest/LLM/Redis/shop_api thật (mẹo test §9, DD Mục 6).

Chạy:  python tests/test_chatbot.py   (từ thư mục chatbot/)
Bước ③④⑤ là code -> assert state kế + tool được gọi; LLM ở ①⑥ để None (fake).
"""

import os
import sys
import time
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
        self.closed_dates = set()   # ISO ngày shop nghỉ -> get_therapists trả rỗng
        self.blocked_phones = set() # SĐT bị chặn NG (A5) — chặn theo từng số
        self.booking_email = None   # email của booking (để xác thực sửa/hủy bằng email — BR-15)
        self.calls = []

    def get_shops(self):
        self.calls.append("shops")
        return [{"id": 1, "name": "Shop A", "address": "1 Rd", "phone": "090-1111"}]

    def get_services(self, shop_id, date, party_size=None):
        self.calls.append("services")
        if date in self.closed_dates:              # A1: ngày shop nghỉ -> 200 rỗng
            return {"courses": [], "addons": [], "reason": "SHOP_CLOSED"}
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
        if date in self.closed_dates:              # ngày shop nghỉ -> không có người trực
            return {"therapists": []}
        return {"therapists": [{"id": 5, "name": "Hana", "gender": "female"}]}

    def get_availability(self, shop_id, date_from, date_to):
        self.calls.append("availability")
        from datetime import date, timedelta
        d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
        open_dates, closed_dates = [], []
        d = d0
        while d <= d1:
            iso = d.isoformat()
            (closed_dates if iso in self.closed_dates else open_dates).append(iso)
            d += timedelta(days=1)
        return {"from": date_from, "to": date_to,
                "open_dates": open_dates, "closed_dates": closed_dates}

    def lookup_customer(self, phone):
        self.calls.append("lookup:" + phone)
        if phone in self.blocked_phones:
            raise ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                               {"reason": "x", "shop_phone": "090-1111"})
        if self.lookup_error:
            raise self.lookup_error
        return {"member_type": "guest", "rank": None, "visit_count": 0}

    def create_booking(self, body):
        self.calls.append("create")
        if self.create_error:
            raise self.create_error
        self.created_body = body
        self.booking_email = body.get("email")     # nhớ email để xác thực sửa/hủy bằng email
        return {"booking_code": "20260723-S001-AB12", "status": "confirmed",
                "edit_token": "tok", "edit_token_expires_in": 120}

    def _check_email(self, email):
        # BR-15: xác thực bằng email -> email KHÔNG khớp thì BE trả 404 BOOKING_NOT_FOUND.
        if self.booking_email is not None and email != self.booking_email:
            raise ShopApiError(404, "BOOKING_NOT_FOUND",
                               "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")

    def patch_booking(self, booking_code, body, edit_token=None):
        self.calls.append("patch:" + booking_code)
        if edit_token is None:                     # đường email (BR-15), không phải X-Edit-Token
            self._check_email(body.get("email"))
        self.patched_body = body
        return {"booking_code": booking_code, "status": "confirmed"}

    def cancel_booking(self, booking_code, email):
        self.calls.append("cancel:" + booking_code)
        self._check_email(email)
        self.cancelled_with = email
        return {"booking_code": booking_code, "status": "cancelled"}


def _settings(support_phone=""):
    return Settings(
        shop_api_base_url="http://x/api/v1",
        llm_base_url="", llm_api_key="", llm_model="m",
        redis_url="", session_ttl_seconds=1800, vault_enc_key="",
        fallback_shop_phone="", support_phone=support_phone,
    )


def _orch(api):
    return Orchestrator(InMemorySessionStore(), api, None, _settings())


def _drive(orch, cid, *messages):
    reply = None
    for m in messages:
        reply = orch.handle_turn(cid, m)
    return reply


def _expire_edit_window(orch, cid):
    """Mô phỏng đã quá cửa sổ nhanh 2': token hết hạn + vault đã bị rút PII (Q5)."""
    ses = orch.store.load(cid)
    ses.edit_token_expires_at = time.time() - 1
    ses.vault = {}
    orch.store.save(ses)


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
    """Đổi course bằng lời -> xóa add-on + slot + confirm (add-on phụ thuộc course, BR-09)."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, course_id=3,
                              guest_addons=[[7]], addons_decided=True, slot="14:00", confirm="yes"))
    sm.merge_params(ses, {"course": "Aroma"})
    check(ses.slots.course_id is None and ses.slots.course_text == "Aroma",
          "đổi course -> bỏ id cũ, giữ tên để map lại qua GET /services")
    check(ses.slots.guest_addons == [] and ses.slots.addons_decided is False, "đổi course phải reset add-on")
    check(ses.slots.slot is None, "đổi course phải xóa slot (BR-07)")
    check(ses.slots.confirm is None, "đổi đơn phải xóa confirm")


_STUB_ADDONS = [{"id": 7, "name": "Foot", "duration_min": 15, "price": 1000,
                 "restricted_course_ids": []}]


def test_group_addons_per_person():
    """BR-10: nhóm cùng course nhưng add-on RIÊNG từng người — hỏi lần lượt từng người."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=3, course_id=3))
    ses.slots.ensure_guest_addons()
    check(sm.next_state(ses) == S.ADDON, "vào bước ADDON")
    ses.slots.addon_texts = ["Foot"]                       # người 1 đọc tên add-on
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "khớp tên -> chọn cho người 1")
    check(ses.slots.addon_guest_idx == 1 and not ses.slots.addons_decided,
          "chọn xong người 1 -> tự sang người 2")
    sm.skip_addon_guest(ses)                               # người 2: "không"
    check(ses.slots.addon_guest_idx == 2, "chuyển sang người 3")
    sm.skip_addon_guest(ses)                               # người 3 (cuối) -> chốt hết
    check(ses.slots.addons_decided is True, "hỏi hết mọi người -> chốt")
    check(ses.slots.guest_addons == [[7], [], []], "add-on lưu RIÊNG từng người")


def test_addon_is_separate_step():
    """Chọn course KHÔNG tự nhảy qua SLOT — phải qua bước ADDON (chốt add-on) trước."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, course_id=3))
    check(sm.next_state(ses) == S.ADDON, "sau course phải vào ADDON, chưa qua SLOT")
    sm.skip_addon_guest(ses)                               # không thêm add-on
    check(sm.next_state(ses) == S.THERAPIST, "chốt add-on -> THERAPIST (party 1) trước SLOT")
    sm.merge_params(ses, {"therapist": "none"})
    check(sm.next_state(ses) == S.SLOT, "chọn người xong mới tới SLOT")


def test_therapist_before_slot_filters():
    """Chỉ định nhân viên TRƯỚC -> SLOT gọi GET /slots lọc theo đúng người đó."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "c7", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không")
    check(r.state == S.THERAPIST, f"phải hỏi nhân viên trước khi chọn giờ, đang {r.state}")
    r = orch.handle_turn("c7", "Hana")           # chỉ định nhân viên id=5
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


def test_greeting_reads_shops():
    """Màn chào: nói rõ là AI, không nút, ĐỌC luôn danh sách cửa hàng, tiếng Việt."""
    api = StubApi()
    orch = _orch(api)
    r = orch.handle_turn("cl", "")                          # mở chat -> câu chào
    check(r.ui["buttons"] == [], "không còn nút lựa chọn nào")
    check("AI" in r.reply_text, "câu chào nói rõ là trợ lý AI (minh bạch APPI)")
    check("Shop A" in r.reply_text, "câu chào ĐỌC luôn danh sách cửa hàng để chọn được ngay")
    check("Anh/chị" in r.reply_text, "câu chào bằng tiếng Việt")


def test_shop_by_name_free_text():
    """Nói tên cửa hàng (không bấm nút) -> map đúng shop_id rồi đi tiếp, không hỏi lại."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("csn", "")
    r = orch.handle_turn("csn", "Shop A")
    check(orch.store.load("csn").slots.shop_id == 1, "nói tên -> map đúng shop_id")
    check(r.state == S.DATE, f"chọn được shop -> hỏi ngày, đang {r.state}")


def test_rule_based_evening_time():
    """Nhánh không-LLM: '7h tối' phải là 19:00, không phải 07:00."""
    from app import nlu
    check(nlu._rule_based("7h tối nay")["entities"]["time"] == "19:00", "'7h tối' -> 19:00")
    check(nlu._rule_based("2h chiều")["entities"]["time"] == "14:00", "'2h chiều' -> 14:00")
    check(nlu._rule_based("9h sáng")["entities"]["time"] == "09:00", "'9h sáng' giữ nguyên 09:00")
    check(nlu._rule_based("16h30")["entities"]["time"] == "16:30", "giờ 24h giữ nguyên")


def test_bare_number_at_party_step():
    """Đang hỏi SỐ NGƯỜI mà khách gõ số trần '3' -> hiểu là 3 người, không hỏi lại.
    (Log thật: LLM trả chitchat/null cho '3', khách phải gõ lại '3 người'.)"""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cbn", "", "Shop A", _FUTURE_DATE)
    r = orch.handle_turn("cbn", "3")
    check(orch.store.load("cbn").slots.party_size == 3, "'3' khi đang hỏi số người -> 3 người")
    check(r.state == S.COURSE, f"hiểu xong -> hỏi gói dịch vụ, đang {r.state}")

    # Số trần > 3 vẫn vào nhánh handoff (BR-14), không set party_size.
    orch2 = _orch(StubApi())
    _drive(orch2, "cbn2", "", "Shop A", _FUTURE_DATE)
    orch2.handle_turn("cbn2", "5")
    check(orch2.store.load("cbn2").slots.party_size is None, "'5' -> quá 3 người, không nhận")


def test_confirm_summary_includes_shop():
    """Tóm tắt ở CONFIRM phải nêu TÊN CỬA HÀNG (khách từng phải gõ 'Thiếu thông tin cửa hàng')."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "csum", "", "Shop A", _FUTURE_DATE, "1 người",
               "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    check(r.state == S.CONFIRM, f"tới bước xác nhận, đang {r.state}")
    check("Shop A" in r.reply_text, "tóm tắt đơn phải có tên cửa hàng")
    check("14:00" in r.reply_text and "Toàn thân" in r.reply_text, "vẫn đủ giờ + gói dịch vụ")


def test_addon_no_does_not_reject_order():
    """'Không' ở bước ADDON = không thêm add-on, KHÔNG được hiểu thành từ chối cả đơn."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cad", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân")
    orch.handle_turn("cad", "không")
    check(orch.store.load("cad").slots.confirm is None,
          "'không' ở ADDON không được set confirm='no'")


def test_unavailable_time_is_announced():
    """Khách nêu giờ đã hết -> bot NÓI RÕ giờ đó hết, không lặng lẽ đọc danh sách khác."""
    api = StubApi()                                    # slots: 14:00, 14:15, 15:00
    orch = _orch(api)
    _drive(orch, "cut", "", "Shop A", _FUTURE_DATE, "1 người",
           "Toàn thân", "không", "ai cũng được")
    r = orch.handle_turn("cut", "19:00")               # giờ shop không có
    check("19:00" in r.reply_text and "không còn trống" in r.reply_text,
          "phải báo rõ giờ khách nêu đã hết")
    check("14:00" in r.reply_text, "vẫn đọc các giờ còn trống để khách chọn lại")
    # Đã báo rồi -> lượt sau không lặp lại câu "19:00 không còn trống".
    r2 = orch.handle_turn("cut", "chọn giúp tôi giờ khác")
    check("19:00 không còn trống" not in r2.reply_text, "không lặp lại thông báo đã nói")


def test_services_not_called_twice_per_turn():
    """Khớp được tên gói -> tiến thẳng sang ADDON; cả hai bước cần /services nhưng chỉ
    được gọi API MỘT lần (log thật: 2 lời gọi y hệt cách nhau 30ms)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "csv", "", "Shop A", _FUTURE_DATE, "1 người")
    before = api.calls.count("services")
    orch.handle_turn("csv", "Toàn thân")               # COURSE khớp -> nhảy sang ADDON
    added = api.calls.count("services") - before
    check(added <= 1, f"không được gọi /services 2 lần trong 1 lượt, đang {added}")


def test_name_matching_tightened():
    """Khớp tên bị siết: input quá ngắn hay mơ hồ (trúng ≥2 tên) -> KHÔNG chọn bừa."""
    shops = [{"id": 1, "name": "Cửa hàng Morioka"},
             {"id": 2, "name": "Cửa hàng Sendai"},
             {"id": 3, "name": "Cửa hàng Tokyo Shibuya"}]

    def _try(text):
        ses = Session(conversation_id="c", turn_count=1)
        ses.slots.shop_text = text
        ok = Orchestrator._match_shop(ses, shops)
        return ok, ses.slots.shop_id

    check(_try("Tokyo") == (True, 3), "'Tokyo' (≥3 ký tự, duy nhất) -> khớp shop 3")
    check(_try("a") == (False, None), "'a' 1 ký tự -> không chọn bừa shop đầu tiên có chữ a")
    check(_try("To") == (False, None), "'To' 2 ký tự không phải nguyên từ -> hỏi lại")
    check(_try("cửa hàng") == (False, None),
          "'cửa hàng' trúng MỌI tên (mơ hồ) -> hỏi lại, không lấy cái đầu tiên")
    check(_try("Cửa hàng Sendai") == (True, 2), "tên đầy đủ -> vẫn khớp bình thường")

    # Course mơ hồ: "Momihogushi" trúng cả 2 mức thời lượng -> không tự chọn mức nào.
    courses = [{"id": 20, "name": "Momihogushi 30"}, {"id": 21, "name": "Momihogushi 60"}]
    ses = Session(conversation_id="c", turn_count=1)
    ses.slots.course_text = "Momihogushi"
    check(Orchestrator._match_course(ses, courses) is False and ses.slots.course_id is None,
          "'Momihogushi' mơ hồ giữa 30/60 phút -> hỏi lại, không tự chọn mức")
    ses.slots.course_text = "Momihogushi 60"
    check(Orchestrator._match_course(ses, courses) is True and ses.slots.course_id == 21,
          "'Momihogushi 60' đủ rõ -> khớp đúng mức 60 phút")


class _HallucinatingLLM:
    """LLM giả trả số bịa — để chứng minh câu chứa số/mã KHÔNG đi qua LLM."""
    def complete(self, *a, **k):
        return "Please call the shop at 019-999-9999 or code 99999999-XX-XX 😊"


def test_literal_renders_never_use_llm():
    """END/HANDOFF/DONE... phải dùng số/mã THẬT từ data, không để LLM bịa (§10)."""
    from app import nlg
    from app.session import Session as Ses
    llm = _HallucinatingLLM()

    p = nlg.build_prompt("END", Ses(conversation_id="c"),
                         {"message": "Số này bị chặn.", "shop_phone": "0258123456"})
    out = nlg.generate(p, llm)
    check("0258123456" in out and "019-999-9999" not in out, "END: số điện thoại phải THẬT")

    ses = Ses(conversation_id="c", booking_code="20260726-S001-AB12")
    p2 = nlg.build_prompt("DONE", ses, {})
    out2 = nlg.generate(p2, llm)
    check("20260726-S001-AB12" in out2 and "99999999" not in out2, "DONE: mã đặt chỗ phải THẬT")

    # Câu hỏi thường (SHOP) vẫn được dùng LLM cho tự nhiên.
    p3 = nlg.build_prompt("SHOP", Ses(conversation_id="c"), {"shops": []})
    check("019-999-9999" in nlg.generate(p3, llm), "SHOP (không số liệu nhạy cảm) vẫn qua LLM")


def test_parse_date_freeform():
    """Hiểu ngày gõ tự do: số trần '31', 'd/m', 'ngày D tháng M', kiểu Nhật, lăn tháng."""
    from datetime import date
    from app import nlu
    t = date(2026, 7, 27)
    check(nlu.parse_date_freeform("31", allow_bare_day=True, today=t) == "2026-07-31",
          "số trần 31 (khi đang hỏi ngày) -> 31/7")
    check(nlu.parse_date_freeform("5", allow_bare_day=True, today=t) == "2026-08-05",
          "mùng 5 đã qua trong tháng -> lăn sang 5/8")
    check(nlu.parse_date_freeform("31", allow_bare_day=False, today=t) is None,
          "không bật bare_day -> số trần KHÔNG bị hiểu là ngày (tránh nhầm số người)")
    check(nlu.parse_date_freeform("31/8", today=t) == "2026-08-31", "'31/8' -> 31 tháng 8")
    check(nlu.parse_date_freeform("ngày 15 tháng 8", today=t) == "2026-08-15", "'ngày 15 tháng 8'")
    check(nlu.parse_date_freeform("2026-08-03", today=t) == "2026-08-03", "ISO giữ nguyên")
    check(nlu.parse_date_freeform("mai", today=t) == "2026-07-28", "tương đối 'mai'")
    check(nlu.parse_date_freeform("31/2", today=t) is None, "'31/2' vô lý -> None")
    check(nlu.parse_date_freeform("99", allow_bare_day=True, today=t) is None, "'99' không phải ngày -> None")


def test_date_freeform_reply_at_date_step():
    """Đang hỏi NGÀY, khách gõ số trần '15' -> hiểu thành ngày, đi tiếp hỏi số người."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cdf", "")
    r = orch.handle_turn("cdf", "Shop A")
    check(r.state == S.DATE, f"sau shop -> hỏi ngày, đang {r.state}")
    r = orch.handle_turn("cdf", "15")
    ses = orch.store.load("cdf")
    check(ses.slots.date is not None, "gõ '15' khi đang hỏi ngày -> đã hiểu thành ngày")
    check(r.state == S.PARTY_SIZE, f"hiểu ngày xong -> hỏi số người, đang {r.state}")


def test_date_question_reads_active_days_only():
    """Câu hỏi ngày chỉ ĐỌC ra ngày shop THỰC SỰ có ca — ngày nghỉ không được mời."""
    from datetime import date, timedelta
    api = StubApi()
    today = date.today()
    d0, d1 = today, today + timedelta(days=1)
    api.closed_dates = {d0.isoformat(), d1.isoformat()}      # hôm nay & mai shop nghỉ
    orch = _orch(api)
    orch.handle_turn("cab", "")
    r = orch.handle_turn("cab", "Shop A")
    check(r.state == S.DATE, f"sau shop -> hỏi ngày, đang {r.state}")
    check(f"{d0.day}/{d0.month}" not in r.reply_text and f"{d1.day}/{d1.month}" not in r.reply_text,
          "ngày shop nghỉ KHÔNG được đọc ra")
    d2 = today + timedelta(days=2)
    check(f"{d2.day}/{d2.month}" in r.reply_text, "vẫn đọc các ngày còn mở (dạng 31/7)")


def test_shop_closed_date_reads_week_days():
    """Chọn ngày shop nghỉ (A1) -> báo 'không phục vụ' + ĐỌC các ngày có làm trong 7 ngày
    tới (không còn nút nên không được nói 'bên dưới')."""
    from datetime import date, timedelta
    api = StubApi()
    today = date.today()
    closed = today + timedelta(days=2)
    api.closed_dates = {closed.isoformat()}
    orch = _orch(api)
    r = _drive(orch, "ca1", "", "Shop A", closed.isoformat(), "1 người")
    check(r.state == S.DATE, f"ngày nghỉ -> hỏi lại ngày, đang {r.state}")
    check("không phục vụ ngày này" in r.reply_text, "báo rõ shop không phục vụ ngày đã chọn")
    check("bên dưới" not in r.reply_text, "không còn nút thì không được nói 'bên dưới'")
    check(f"{today.day}/{today.month}" in r.reply_text,
          "đọc các ngày có làm trong 7 ngày tới (dạng 31/7)")


def test_shop_closed_all_days_routes_back_to_shop():
    """Shop nghỉ suốt 2 tuần tới -> quay lại chọn cửa hàng khác (không kẹt ở bước ngày)."""
    from datetime import date, timedelta
    api = StubApi()
    today = date.today()
    api.closed_dates = {(today + timedelta(days=i)).isoformat()
                        for i in range(Orchestrator._AVAIL_HORIZON_DAYS)}
    orch = _orch(api)
    orch.handle_turn("csc", "")
    r = orch.handle_turn("csc", "Shop A")
    check(r.state == S.SHOP, f"shop nghỉ hết -> quay lại chọn shop, đang {r.state}")
    check(orch.store.load("csc").slots.shop_id is None, "bỏ shop đã chọn để khách chọn lại")


def test_contact_asks_only_missing():
    """Đã cho số điện thoại -> chỉ hỏi email, không hỏi lại cả hai."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    p = nlg.build_prompt("CONTACT", Ses(conversation_id="c", slots=Sl(phone="{{phone_1}}")), {})
    check(p["facts"]["hoi"] == "email", "đã có SĐT -> chỉ hỏi email")
    p2 = nlg.build_prompt("CONTACT", Ses(conversation_id="c"), {})
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
_HAPPY = ("", "Shop A", _FUTURE_DATE, "1 người",
          "Toàn thân", "không", "ai cũng được", "14:00",
          "0901234567 a@b.com", "đồng ý đặt")


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


def test_group_flow_addons_per_person():
    """Nhóm 3 người, 1 course, add-on RIÊNG từng người -> body reservations khác nhau (BR-10)."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "cg",
               "", "Shop A", _FUTURE_DATE, "3 người", "Toàn thân",
               "Foot", "addon:done",     # người 1: add-on 7
               "không",                # người 2: không thêm
               "Foot", "addon:done",     # người 3: add-on 7
               "14:00", "0901234567 a@b.com", "đồng ý đặt")
    check(r.state == S.DONE, f"nhóm đặt xong -> DONE, đang {r.state}")
    res = [x["addon_ids"] for x in api.created_body["reservations"]]
    check(res == [[7], [], [7]], "reservations add-on RIÊNG từng người (BR-10)")
    check(api.created_body["party_size"] == 3, "đúng 3 người, cùng course")


def test_a5_phone_blocked():
    api = StubApi()
    api.lookup_error = ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                                    {"reason": "abc", "shop_phone": "090-1111"})
    orch = _orch(api)
    reply = _drive(orch, "c2",
                   "", "Shop A", _FUTURE_DATE, "1 người",
                   "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    # A5 chặn theo TỪNG số -> cho thử số khác (quay lại CONTACT), KHÔNG kết thúc/đặt.
    check(reply.state == S.CONTACT, f"A5: cho thử số khác (CONTACT), đang {reply.state}")
    check(api.created_body is None, "A5: KHÔNG được tạo booking")
    check("090-1111" in reply.reply_text, "A5: đưa số hỗ trợ")


def test_a6_slot_conflict():
    api = StubApi()
    api.create_error = ShopApiError(409, "SLOT_CONFLICT", "Giờ vừa hết.",
                                    {"suggested_slots": ["14:30", "15:15"]})
    orch = _orch(api)
    reply = _drive(orch, "c3", *_HAPPY)
    check(reply.state == S.SLOT, f"A6: quay lại SLOT, đang {reply.state}")
    check("14:30" in reply.reply_text and "15:15" in reply.reply_text,
          "A6: ĐỌC suggested_slots ra cho khách chọn lại")


def test_handoff_reads_phone():
    """Xin gặp người thật -> đọc số điện thoại ra (không còn nút gọi)."""
    api = StubApi()
    orch = _orch(api)
    reply = _drive(orch, "c4", "", "Shop A", "cho tôi gặp nhân viên")
    check("090-1111" in reply.reply_text, "handoff: phải đọc số cửa hàng cho khách gọi")


def test_modify_slot_in_session():
    """Sau khi đặt xong, sửa giờ trong phiên -> PATCH với giờ mới (UC-02, BR-17)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c5", *_HAPPY)                       # đặt xong -> DONE
    menu = orch.handle_turn("c5", "sửa lịch")
    check(menu.state == S.DONE and orch.store.load("c5").editing is True,
          "modify: nói 'sửa lịch' -> vào chế độ sửa, hỏi đổi phần nào")
    orch.handle_turn("c5", "đổi giờ")             # -> quay lại SLOT
    orch.handle_turn("c5", "14:15")              # chọn giờ mới -> CONFIRM
    reply = orch.handle_turn("c5", "đồng ý đặt")     # đồng ý -> PATCH
    check(api.patched_body is not None, "modify: phải gọi PATCH")
    check(api.patched_body["start_time"] == "14:15", "modify: PATCH đúng giờ mới")
    check(reply.state == S.DONE, "modify: xong quay lại DONE")


def test_cancel_in_session():
    """Sau khi đặt xong, hủy trong phiên -> cancel với email thật (UC-03)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c6", *_HAPPY)
    reply = orch.handle_turn("c6", "hủy lịch")
    check(api.cancelled_with == "a@b.com", "cancel: gửi email THẬT (đã unmask)")
    check(reply.state == S.CANCELLED and reply.done is True, "cancel: state CANCELLED, done")


def test_modify_party_and_course_reset_addons():
    """Sửa số người / đổi course phải XÓA add-on cũ (BR-10). Trước đây modify:course gán
    s.addons=[] (field không tồn tại) nên add-on không hề reset — nay reset đúng."""
    def _booked_group():
        return Session(conversation_id="c", turn_count=1, booking_code="X", editing=False,
                       slots=Slots(shop_id=1, date="d", party_size=2, course_id=3,
                                   guest_addons=[[7], [8]], addons_decided=True, addon_guest_idx=1))

    ses = _booked_group()
    sm.apply_modify_target(ses, "party")
    s = ses.slots
    check(s.party_size is None, "đổi số người -> xóa số người")
    check(s.guest_addons == [] and s.addons_decided is False and s.addon_guest_idx == 0,
          "đổi số người -> reset add-on về người 1")

    ses2 = _booked_group()
    sm.apply_modify_target(ses2, "course")
    s2 = ses2.slots
    check(s2.course_id is None, "đổi dịch vụ -> xóa course")
    check(s2.guest_addons == [] and s2.addons_decided is False and s2.addon_guest_idx == 0,
          "đổi dịch vụ -> reset add-on đúng field (không còn set nhầm s.addons)")


def test_addon_group_prompt_shows_person_index():
    """Câu hỏi add-on khi đặt NHÓM phải nêu rõ 'Người n/m' — không để LLM bỏ mất (khiến
    người 2 nhìn giống hỏi lại người 1)."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ses = Ses(conversation_id="c", slots=Sl(party_size=2, course_id=3, guest_addons=[[7], []], addon_guest_idx=1))
    p = nlg.build_prompt("ADDON", ses, {"addons": []})
    out = nlg.generate(p, _HallucinatingLLM())     # dù có LLM, ADDON vẫn ép template tất định
    check("Người 2/2" in out, "ADDON nhóm phải nêu rõ đang hỏi Người 2/2")


def test_addon_prompt_reads_list_and_hides_restricted():
    """Câu hỏi add-on phải ĐỌC tên add-on ra (không còn nút), và ẨN add-on bị cấm với
    course đang chọn (BR-09) để không mời nhầm."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ar = {"addons": [
        {"id": 7, "name": "Aroma Oil", "duration_min": 30, "price": 1500,
         "restricted_course_ids": []},
        {"id": 8, "name": "Hot Stone", "duration_min": 15, "price": 1000,
         "restricted_course_ids": [3]},          # cấm với course 3
    ]}
    ses = Ses(conversation_id="c", slots=Sl(party_size=1, course_id=3, guest_addons=[[]], addon_guest_idx=0))
    out = nlg.generate(nlg.build_prompt("ADDON", ses, ar), None)
    check("Aroma Oil" in out, "đọc tên add-on hợp lệ ra cho khách chọn")
    check("Hot Stone" not in out, "add-on bị cấm với course đang chọn KHÔNG được mời (BR-09)")
    check("không" in out.lower(), "có hướng dẫn nói 'không' để bỏ qua")


def test_match_addons_rejects_restricted():
    """Khách đọc tên add-on bị cấm với course -> KHÔNG nhận (BR-09), hỏi lại."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(party_size=1, course_id=3, guest_addons=[[]]))
    ses.slots.addon_texts = ["Hot Stone"]
    ok = Orchestrator._match_addons(ses, [{"id": 8, "name": "Hot Stone", "duration_min": 15,
                                           "restricted_course_ids": [3]}])
    check(ok is False, "add-on cấm -> không khớp")
    check(ses.slots.guest_addons == [[]], "không gán add-on cấm cho khách")


def test_modify_party_in_session():
    """Sửa số người 1->2 trong phiên: hỏi lại add-on RIÊNG từng người rồi PATCH đúng (BR-10)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cmp", *_HAPPY)                       # đặt 1 người xong
    orch.handle_turn("cmp", "sửa lịch")
    orch.handle_turn("cmp", "đổi số người")
    r = orch.handle_turn("cmp", "2 người")
    check(r.state == S.ADDON, f"đổi số người -> hỏi lại add-on, đang {r.state}")
    orch.handle_turn("cmp", "Foot")                 # người 1: add-on 7
    orch.handle_turn("cmp", "addon:done")              # xong người 1 -> người 2
    r = orch.handle_turn("cmp", "không")          # người 2: không thêm -> chốt
    check(r.state == S.SLOT, f"chốt add-on 2 người -> chọn giờ, đang {r.state}")
    orch.handle_turn("cmp", "14:15")
    r = orch.handle_turn("cmp", "đồng ý đặt")
    check(api.patched_body is not None, "modify party: phải gọi PATCH")
    check(api.patched_body["party_size"] == 2, "PATCH đúng 2 người")
    check([x["addon_ids"] for x in api.patched_body["reservations"]] == [[7], []],
          "PATCH add-on RIÊNG từng người sau khi sửa số người (BR-10)")
    check(r.state == S.DONE, "modify party xong -> DONE")


def test_modify_keep_returns_to_done():
    """'Giữ nguyên' ở menu sửa -> về DONE, tắt editing, KHÔNG ghi gì."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "ck", *_HAPPY)
    orch.handle_turn("ck", "sửa lịch")
    r = orch.handle_turn("ck", "giữ nguyên")
    check(r.state == S.DONE, f"'Giữ nguyên' -> quay lại DONE, đang {r.state}")
    check(orch.store.load("ck").editing is False, "modify:keep tắt cờ editing")
    check(api.patched_body is None, "modify:keep KHÔNG gọi PATCH")


def test_cancel_by_text():
    """Hủy bằng LỜI sau khi đặt xong (không bấm nút) -> cancel với email thật (UC-03)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "ct", *_HAPPY)
    r = orch.handle_turn("ct", "hủy lịch giúp tôi")
    check(api.cancelled_with == "a@b.com", "hủy bằng lời -> cancel email THẬT")
    check(r.state == S.CANCELLED and r.done is True, "hủy bằng lời -> CANCELLED")


class _NonJsonLLM:
    """Router 'nói' thay vì trích JSON — mô phỏng LLM lờ chỉ dẫn 'chỉ trả JSON'."""
    def complete(self, *a, **k):
        return "Dạ được ạ, em đặt lịch cho anh/chị ngay!"


def test_nlu_falls_back_when_llm_not_json():
    """LLM trả text thường -> KHÔNG trả None (khỏi REPROMPT oan); rule-based bắt được ý."""
    from app import nlu
    parsed = nlu.extract("đồng ý đặt", _NonJsonLLM())
    check(parsed is not None, "router trả text -> extract vẫn có kết quả (không None)")
    check(parsed["entities"]["confirm"] == "yes", "rule-based bắt 'đồng ý' -> confirm=yes")


def test_confirm_by_text_when_llm_flaky_books():
    """Ở CONFIRM, gõ 'đồng ý đặt' khi router hỏng (trả text) -> vẫn đặt được, không REPROMPT."""
    api = StubApi()
    orch = Orchestrator(InMemorySessionStore(), api, _NonJsonLLM(), _settings())
    _drive(orch, "cflaky", "", "Shop A", _FUTURE_DATE, "1 người",
           "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    r = orch.handle_turn("cflaky", "đồng ý đặt")
    check(api.created_body is not None, "xác nhận bằng lời (LLM hỏng) vẫn đặt được")
    check(r.state == S.DONE, f"-> DONE, đang {r.state}")


def test_support_phone_env_takes_priority():
    """Số hỗ trợ/CSKH ở env được ưu tiên khi chặn NG (A5), thay số cửa hàng do BE trả."""
    api = StubApi()
    api.lookup_error = ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                                    {"reason": "x", "shop_phone": "090-1111"})
    orch = Orchestrator(InMemorySessionStore(), api, None, _settings(support_phone="1900-6068"))
    reply = _drive(orch, "csp", "", "Shop A", _FUTURE_DATE, "1 người",
                   "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    check(reply.state == S.CONTACT, "A5 -> cho thử số khác (CONTACT)")
    check("1900-6068" in reply.reply_text, "hiện số hỗ trợ env")
    check("090-1111" not in reply.reply_text, "không hiện số cửa hàng khi đã có số hỗ trợ env")


def test_a5_retry_with_another_phone_books():
    """Số bị chặn -> nhập SỐ KHÁC (không nhập lại email) -> đặt được; email giữ nguyên, KHÔNG
    gửi placeholder rác (bug user: bấm 'Đồng ý đặt' vẫn 'chưa hiểu rõ')."""
    api = StubApi()
    api.blocked_phones = {"0779776153"}
    orch = _orch(api)
    _drive(orch, "cr", "", "Shop A", _FUTURE_DATE, "1 người",
           "Toàn thân", "không", "ai cũng được", "14:00",
           "phamvinh324@gmail.com 0779776153")          # email + số bị chặn
    ses = orch.store.load("cr")
    check(ses.state == S.CONTACT, f"số bị chặn -> xin số khác (CONTACT), đang {ses.state}")
    check(ses.slots.email is not None and ses.vault, "email + vault CÒN nguyên (không bị rút)")
    orch.handle_turn("cr", "0779776154")                # SỐ KHÁC, không nhập lại email
    r = orch.handle_turn("cr", "đồng ý đặt")
    check(api.created_body is not None, "số khác không bị chặn -> đặt được")
    check(api.created_body["phone"] == "0779776154", "gửi đúng số mới")
    check(api.created_body["email"] == "phamvinh324@gmail.com",
          "email THẬT (không phải '{{email_1}}')")
    check(r.state == S.DONE, "đặt xong -> DONE")


def test_create_guard_reasks_when_pii_stale():
    """Chốt chặn: PII placeholder không giải được (vault rút) -> KHÔNG gửi rác cho BE, xin lại."""
    api = StubApi()
    orch = _orch(api)
    ses = Session(conversation_id="cg", turn_count=1,
                  slots=Slots(shop_id=1, date=_FUTURE_DATE, party_size=1, course_id=3,
                              addons_decided=True, slot="14:00", therapist_decided=True,
                              phone="{{phone_1}}", email="{{email_1}}",
                              contact_verified=True, confirm="yes"))
    ses.vault = {"{{phone_1}}": "0901234567"}            # email_1 KHÔNG có trong vault
    res = orch._create_booking(ses)
    check(api.created_body is None, "không gửi booking khi email placeholder chưa giải được")
    check(ses.state == S.CONTACT, "quay lại CONTACT xin lại liên hệ")
    check(ses.slots.email is None, "reset email để hỏi lại; giữ phone đã giải được")


def test_modify_after_2min_reasks_email_then_updates():
    """Sửa lịch SAU cửa sổ 2' (vault đã rút) -> xin lại email để xác thực rồi PATCH (BR-15)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c2m", *_HAPPY)                       # đặt xong
    _expire_edit_window(orch, "c2m")                   # quá 2', vault rút
    orch.handle_turn("c2m", "sửa lịch")
    orch.handle_turn("c2m", "đổi giờ")             # đổi giờ
    orch.handle_turn("c2m", "14:15")
    r = orch.handle_turn("c2m", "đồng ý đặt")         # token hết + vault rút -> xin email
    check(api.patched_body is None, "chưa PATCH khi chưa có email")
    check("email" in r.reply_text.lower(), "phải xin lại email để xác thực")
    check(orch.store.load("c2m").awaiting_edit_email is True, "đang chờ email")
    r = orch.handle_turn("c2m", "a@b.com")             # khách nhập lại email
    check(api.patched_body is not None, "có email -> PATCH")
    check(api.patched_body["start_time"] == "14:15", "PATCH đúng giờ mới")
    check(api.patched_body.get("email") == "a@b.com", "PATCH kèm email xác thực (BR-15)")
    check(r.state == S.DONE, "sửa xong -> DONE")


def test_cancel_after_2min_reasks_email():
    """Hủy lịch SAU cửa sổ 2' -> xin lại email rồi hủy (không đẩy sang trang Quản lý)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "c2c", *_HAPPY)
    _expire_edit_window(orch, "c2c")
    r = orch.handle_turn("c2c", "hủy lịch")        # token hết + vault rút -> xin email
    check(api.cancelled_with is None, "chưa hủy khi chưa có email")
    check(orch.store.load("c2c").awaiting_edit_email is True, "đang chờ email để hủy")
    r = orch.handle_turn("c2c", "a@b.com")
    check(api.cancelled_with == "a@b.com", "có email -> hủy với email THẬT")
    check(r.state == S.CANCELLED, "hủy xong -> CANCELLED")


def test_edit_after_2min_wrong_email_then_correct():
    """Sau 2', nhập email SAI -> báo lỗi + VẪN xin lại (không kẹt); nhập ĐÚNG -> sửa được."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cw", *_HAPPY)                        # booking email = a@b.com
    _expire_edit_window(orch, "cw")
    orch.handle_turn("cw", "sửa lịch")
    orch.handle_turn("cw", "đổi giờ")
    orch.handle_turn("cw", "14:15")
    orch.handle_turn("cw", "đồng ý đặt")              # -> xin email
    orch.handle_turn("cw", "wrong@b.com")              # email SAI
    check(api.patched_body is None, "email sai -> chưa PATCH")
    ses = orch.store.load("cw")
    check(ses.awaiting_edit_email is True, "email sai -> VẪN chờ email (không kẹt)")
    check(ses.slots.email is None, "email sai -> reset để nhập lại")
    r = orch.handle_turn("cw", "a@b.com")              # email ĐÚNG
    check(api.patched_body is not None, "email đúng -> PATCH thành công")
    check(api.patched_body.get("email") == "a@b.com", "PATCH dùng ĐÚNG email (không kẹt email sai)")
    check(r.state == S.DONE, "sửa xong -> DONE")


def test_summary_hides_unresolved_phone_placeholder():
    """Vault đã rút -> tóm tắt CONFIRM KHÔNG rò rỉ '{{phone_1}}' (bug user thấy)."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ses = Ses(conversation_id="c", slots=Sl(date=_FUTURE_DATE, slot="14:00", party_size=1, course_name="C",
                       phone="{{phone_1}}"))
    ses.vault = {}                                     # vault rút -> phone không giải được
    summ = nlg.build_prompt("CONFIRM", ses, {})["facts"]["summary"]
    check("{{phone_1}}" not in summ, "không rò rỉ placeholder khi vault đã rút")

    ses2 = Ses(conversation_id="c", slots=Sl(date=_FUTURE_DATE, slot="14:00", party_size=1, course_name="C",
                        phone="{{phone_1}}"))
    ses2.vault = {"{{phone_1}}": "0901234567"}
    summ2 = nlg.build_prompt("CONFIRM", ses2, {})["facts"]["summary"]
    check("{{phone_1}}" in summ2, "vault còn -> vẫn nêu SĐT (placeholder, unmask sau)")


def test_done_shows_quick_edit_countdown():
    """Đặt xong hiện đồng hồ 'sửa nhanh còn ~m:ss' (BR-17); menu Sửa lịch cũng nhắc lại."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "cq", *_HAPPY)
    check(r.state == S.DONE, "đặt xong -> DONE")
    check("Sửa/hủy nhanh" in r.reply_text and ":" in r.reply_text,
          "DONE hiện đồng hồ sửa nhanh (m:ss)")
    menu = orch.handle_turn("cq", "sửa lịch")
    check("Sửa/hủy nhanh" in menu.reply_text, "menu MODIFY cũng nhắc cửa sổ sửa nhanh")


def test_quick_edit_note_live_and_expired():
    """Còn giờ -> 'còn khoảng m:ss'; hết 2' -> nhắc cần nhập lại email."""
    from app import nlg
    from app.session import Session as Ses
    ses = Ses(conversation_id="c", booking_code="X",
              edit_token_expires_at=time.time() + 90)
    check("còn khoảng" in nlg._quick_edit_note(ses), "còn thời gian -> 'còn khoảng m:ss'")
    ses.edit_token_expires_at = time.time() - 1
    check("cần nhập lại email" in nlg._quick_edit_note(ses), "hết 2' -> nhắc cần email")
    ses.edit_token_expires_at = None
    check(nlg._quick_edit_note(ses) == "", "chưa có mốc -> không hiện gì")


def test_reply_reads_choice_lists():
    """Câu trả lời (tiếng Việt) phải ĐỌC đủ danh sách lựa chọn ra — không còn nút."""
    from app import nlg
    from app.session import Session as Ses

    ar = {"shops": [{"name": "Shop A"}, {"name": "Shop B"}]}
    out = nlg.generate(nlg.build_prompt("SHOP", Ses(conversation_id="c"), ar), None)
    check("Shop A" in out and "Shop B" in out, "đọc đủ tên cửa hàng")
    check("Anh/chị" in out, "câu bằng tiếng Việt")


def test_slot_by_spoken_time():
    """Nói giờ còn trống -> chốt luôn; nói giờ đã kín -> mời chọn lại, không chốt bừa."""
    api = StubApi()                                    # slots: 14:00, 14:15, 15:00
    orch = _orch(api)
    _drive(orch, "cst", "", "Shop A", _FUTURE_DATE, "1 người",
           "Toàn thân", "không", "ai cũng được")
    r = orch.handle_turn("cst", "16:30")               # giờ KHÔNG có trong danh sách
    check(orch.store.load("cst").slots.slot is None, "giờ không trống -> không chốt")
    check(r.state == S.SLOT, "vẫn ở bước chọn giờ")
    check("14:00" in r.reply_text, "đọc lại các giờ còn trống")
    r = orch.handle_turn("cst", "14:15")               # giờ CÓ trong danh sách
    check(orch.store.load("cst").slots.slot == "14:15", "nói đúng giờ trống -> chốt luôn")



def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{_PASSED} checks passed across {len(tests)} tests.")


if __name__ == "__main__":
    run_all()
