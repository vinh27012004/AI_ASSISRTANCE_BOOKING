"""Test offline — không cần pytest/LLM/Redis/shop_api thật (mẹo test §9, DD Mục 6).

Chạy:  python tests/test_chatbot.py   (từ thư mục chatbot/)
Bước ③④⑤ là code -> assert state kế + tool được gọi; LLM ở ①⑥ để None (fake).
"""

import os
import sys
import time
from dataclasses import asdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ngày trong tương lai để bộ lọc "bỏ giờ đã qua của HÔM NAY" không đụng tới slot cố định.
_FUTURE_DATE = (date.today() + timedelta(days=2)).isoformat()

from app import matching
from app import nlu
from app import pii
from app import retrieval
from app import state_machine as sm
from app import states as S
from app.answers import faq
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
        self.dead_shops = set()     # shop_id KHÔNG có ca ngày nào (như Cửa hàng ABC thật)
        self.blocked_phones = set() # SĐT bị chặn NG (A5) — chặn theo từng số
        self.booking_email = None   # email của booking (để xác thực sửa/hủy bằng email — BR-15)
        self.calls = []

    def get_shops(self):
        self.calls.append("shops")
        return [
            {"id": 1, "name": "Shop A", "address": "25 Hàng Bài, Hoàn Kiếm, Hà Nội",
             "phone": "024 3826 1301"},
            {"id": 2, "name": "Cửa hàng Hải Châu", "address": "88 Bạch Đằng, Hải Châu, Đà Nẵng",
             "phone": "0236 3812 1302"},
        ]

    # Ca làm KHÁC nhau giữa 2 shop -> hỏi "mở lúc 19:00" phân biệt được (chỉ shop 2 còn làm).
    def get_timeline(self, shop_id, date):
        self.calls.append("timeline")
        if shop_id in self.dead_shops or date in self.closed_dates:
            return {"date": date, "therapists": []}
        hours = ("12:00", "21:00") if shop_id == 2 else ("10:00", "18:00")
        # Đội ngũ KHÁC nhau giữa 2 shop -> test lọc theo số lượng + giới tính.
        roster = ([(5, "Thu Hà", "female"), (6, "Ngọc Mai", "female"), (7, "Minh Khôi", "male")]
                  if shop_id == 2 else [(5, "Thu Hà", "female")])
        return {"date": date, "therapists": [{
            "id": tid, "name": nm, "gender": g,
            "shifts": [{"start_time": hours[0], "end_time": hours[1]}], "bookings": [],
        } for tid, nm, g in roster]}

    def get_services(self, shop_id, date, party_size=None):
        self.calls.append("services")
        if shop_id in self.dead_shops or date in self.closed_dates:   # A1: 200 rỗng
            return {"courses": [], "addons": [], "reason": "SHOP_CLOSED"}
        return {"courses": [{"id": 3, "name": "Toàn thân", "duration_min": 60, "price": 350000}],
                "addons": [{"id": 7, "name": "Bấm huyệt", "duration_min": 15, "price": 80000,
                            "restricted_course_ids": []},
                           {"id": 8, "name": "Đá nóng", "duration_min": 15, "price": 90000,
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
        return {"therapists": [{"id": 5, "name": "Thu Hà", "gender": "female"}]}

    def get_availability(self, shop_id, date_from, date_to):
        self.calls.append("availability")
        from datetime import date, timedelta
        d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
        open_dates, closed_dates = [], []
        d = d0
        while d <= d1:
            iso = d.isoformat()
            shut = shop_id in self.dead_shops or iso in self.closed_dates
            (closed_dates if shut else open_dates).append(iso)
            d += timedelta(days=1)
        return {"from": date_from, "to": date_to,
                "open_dates": open_dates, "closed_dates": closed_dates}

    def lookup_customer(self, phone):
        self.calls.append("lookup:" + phone)
        if phone in self.blocked_phones:
            raise ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                               {"reason": "x", "shop_phone": "024 3826 1301"})
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
                              addon_ids=[7], addons_decided=True, slot="14:00", confirm="yes"))
    sm.merge_params(ses, {"course": "Tinh dầu"})
    check(ses.slots.course_id is None and ses.slots.course_text == "Tinh dầu",
          "đổi course -> bỏ id cũ, giữ tên để map lại qua GET /services")
    check(ses.slots.addon_ids == [] and ses.slots.addons_decided is False, "đổi course phải reset add-on")
    check(ses.slots.slot is None, "đổi course phải xóa slot (BR-07)")
    check(ses.slots.confirm is None, "đổi đơn phải xóa confirm")


_STUB_ADDONS = [{"id": 7, "name": "Bấm huyệt", "duration_min": 15, "price": 80000,
                 "restricted_course_ids": []},
                {"id": 8, "name": "Đá nóng", "duration_min": 15, "price": 90000,
                 "restricted_course_ids": []}]


def test_group_shares_addons():
    """BR-10 (BA cập nhật): nhóm dùng CHUNG course và add-on — hỏi MỘT lần cho cả nhóm."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=3, course_id=3))
    check(sm.next_state(ses) == S.ADDON, "vào bước ADDON")
    ses.slots.addon_texts = ["Bấm huyệt"]
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "khớp tên -> chốt cho cả nhóm")
    check(ses.slots.addon_ids == [7], "một danh sách add-on dùng chung")
    check(ses.slots.addons_decided is True, "hỏi MỘT lần là xong, không lặp theo từng người")
    check(sm.next_state(ses) == S.SLOT, "nhóm ≥2 bỏ qua THERAPIST -> sang chọn giờ")


def test_multiple_addons_in_one_sentence():
    """Web tick được nhiều add-on -> chat cũng phải nhận nhiều tên trong MỘT câu.
    (Trước đây _pick_unique thấy 2 tên khớp thì coi là mơ hồ rồi bỏ sạch.)"""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, course_id=3))
    ses.slots.addon_texts = ["cho tôi Bấm huyệt với Đá nóng"]        # nhánh không-LLM: cả câu
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "phải khớp được")
    check(ses.slots.addon_ids == [7, 8], f"nhận CẢ HAI add-on, đang {ses.slots.addon_ids}")

    ses2 = Session(conversation_id="c", turn_count=1,
                   slots=Slots(shop_id=1, date="d", party_size=1, course_id=3))
    ses2.slots.addon_texts = ["Bấm huyệt", "Đá nóng"]                # nhánh LLM: danh sách tên
    check(Orchestrator._match_addons(ses2, _STUB_ADDONS) is True, "dạng danh sách cũng khớp")
    check(ses2.slots.addon_ids == [7, 8], "nhận cả hai")


def test_addon_is_separate_step():
    """Chọn course KHÔNG tự nhảy qua SLOT — phải qua bước ADDON (chốt add-on) trước."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, course_id=3))
    check(sm.next_state(ses) == S.ADDON, "sau course phải vào ADDON, chưa qua SLOT")
    sm.skip_addons(ses)                                    # không thêm add-on
    check(sm.next_state(ses) == S.THERAPIST, "chốt add-on -> THERAPIST (party 1) trước SLOT")
    sm.merge_params(ses, {"therapist": "none"})
    check(sm.next_state(ses) == S.SLOT, "chọn người xong mới tới SLOT")


def test_therapist_before_slot_filters():
    """Chỉ định nhân viên TRƯỚC -> SLOT gọi GET /slots lọc theo đúng người đó."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "c7", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không")
    check(r.state == S.THERAPIST, f"phải hỏi nhân viên trước khi chọn giờ, đang {r.state}")
    r = orch.handle_turn("c7", "Thu Hà")           # chỉ định nhân viên id=5
    check(r.state == S.SLOT, "chọn người xong mới tới SLOT")
    check(api.last_slots_kw.get("therapist_id") == 5, "SLOT phải lọc giờ theo nhân viên đã chọn")


# --------------------------------------------------------------------------- #
#  PII                                                                         #
# --------------------------------------------------------------------------- #
def test_match_therapist_by_name():
    """Khách nêu tên 'Thu Hà' -> map về therapist_id, không hỏi lại (bug user báo)."""
    from app.orchestrator import Orchestrator
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=1, therapist_text="Thu Hà"))
    ok = Orchestrator._match_therapist(ses, [{"id": 5, "name": "Thu Hà", "gender": "female"}])
    check(ok is True, "phải khớp tên Thu Hà")
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
    shops = [{"id": 1, "name": "Cửa hàng Hoàn Kiếm"},
             {"id": 2, "name": "Cửa hàng Hải Châu"},
             {"id": 3, "name": "Cửa hàng Sài Gòn"}]

    def _try(text):
        ses = Session(conversation_id="c", turn_count=1)
        ses.slots.shop_text = text
        ok = Orchestrator._match_shop(ses, shops)
        return ok, ses.slots.shop_id

    check(_try("Sài Gòn") == (True, 3), "'Sài Gòn' (≥3 ký tự, duy nhất) -> khớp shop 3")
    check(_try("a") == (False, None), "'a' 1 ký tự -> không chọn bừa shop đầu tiên có chữ a")
    check(_try("To") == (False, None), "'To' 2 ký tự không phải nguyên từ -> hỏi lại")
    check(_try("cửa hàng") == (False, None),
          "'cửa hàng' trúng MỌI tên (mơ hồ) -> hỏi lại, không lấy cái đầu tiên")
    check(_try("Cửa hàng Hải Châu") == (True, 2), "tên đầy đủ -> vẫn khớp bình thường")

    # Course mơ hồ: "Massage body" trúng cả 2 mức thời lượng -> không tự chọn mức nào.
    courses = [{"id": 20, "name": "Massage body 30"}, {"id": 21, "name": "Massage body 60"}]
    ses = Session(conversation_id="c", turn_count=1)
    ses.slots.course_text = "Massage body"
    check(Orchestrator._match_course(ses, courses) is False and ses.slots.course_id is None,
          "'Massage body' mơ hồ giữa 30/60 phút -> hỏi lại, không tự chọn mức")
    ses.slots.course_text = "Massage body 60"
    check(Orchestrator._match_course(ses, courses) is True and ses.slots.course_id == 21,
          "'Massage body 60' đủ rõ -> khớp đúng mức 60 phút")


class _HallucinatingLLM:
    """LLM giả trả số bịa — để chứng minh câu chứa số/mã KHÔNG đi qua LLM."""
    def complete(self, *a, **k):
        return "Please call the shop at 0999 999 999 or code 99999999-XX-XX 😊"


def test_literal_renders_never_use_llm():
    """END/HANDOFF/DONE... phải dùng số/mã THẬT từ data, không để LLM bịa (§10)."""
    from app import nlg
    from app.session import Session as Ses
    llm = _HallucinatingLLM()

    p = nlg.build_prompt("END", Ses(conversation_id="c"),
                         {"message": "Số này bị chặn.", "shop_phone": "0258123456"})
    out = nlg.generate(p, llm)
    check("0258123456" in out and "0999 999 999" not in out, "END: số điện thoại phải THẬT")

    ses = Ses(conversation_id="c", booking_code="20260726-S001-AB12")
    p2 = nlg.build_prompt("DONE", ses, {})
    out2 = nlg.generate(p2, llm)
    check("20260726-S001-AB12" in out2 and "99999999" not in out2, "DONE: mã đặt chỗ phải THẬT")

    # SHOP/COURSE/CONFIRM chuyển sang tất định (26/8) — chúng đọc lại danh sách và giá LẤY
    # TỪ API, đúng loại số liệu quy tắc này bảo vệ. Đổi lại còn giúp bỏ ~11 lượt gọi LLM
    # mỗi phiên và giữ xưng hô nhất quán (LLM tự nhảy "Quý khách"/"Bạn" giữa chừng).
    p3 = nlg.build_prompt("SHOP", Ses(conversation_id="c"),
                          {"shops": [{"name": "Cửa hàng Hải Châu"}]})
    out3 = nlg.generate(p3, llm)
    check("0999 999 999" not in out3, "SHOP không được để LLM viết lại")
    check("Cửa hàng Hải Châu" in out3, f"SHOP phải đọc đúng tên từ API: {out3!r}")

    p4 = nlg.build_prompt("COURSE", Ses(conversation_id="c"),
                          {"courses": [{"name": "Toàn thân", "duration_min": 60, "price": 350000}]})
    out4 = nlg.generate(p4, llm)
    check("0999 999 999" not in out4 and "350.000₫" in out4, f"COURSE: giá phải THẬT: {out4!r}")

    # GREETING/REPROMPT cố ý VẪN qua LLM (không chứa số liệu, cần câu chữ đa dạng).
    p5 = nlg.build_prompt("GREETING", Ses(conversation_id="c"), {})
    check("0999 999 999" in nlg.generate(p5, llm), "GREETING vẫn qua LLM")


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


def test_group_flow_shared_addons():
    """Nhóm 3 người: cùng course, cùng add-on -> mọi reservation có CÙNG addon_ids (BR-10)."""
    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "cg",
               "", "Shop A", _FUTURE_DATE, "3 người", "Toàn thân",
               "Bấm huyệt với Đá nóng",              # add-on chọn MỘT lần, dùng chung cả nhóm
               "14:00", "0901234567 a@b.com", "đồng ý đặt")
    check(r.state == S.DONE, f"nhóm đặt xong -> DONE, đang {r.state}")
    res = [x["addon_ids"] for x in api.created_body["reservations"]]
    check(res == [[7, 8], [7, 8], [7, 8]],
          f"cả nhóm CÙNG add-on (BR-10 BA cập nhật), đang {res}")
    check(api.created_body["party_size"] == 3, "đúng 3 người, cùng course")


def test_a5_phone_blocked():
    api = StubApi()
    api.lookup_error = ShopApiError(403, "PHONE_BLOCKED", "SĐT bị chặn.",
                                    {"reason": "abc", "shop_phone": "024 3826 1301"})
    orch = _orch(api)
    reply = _drive(orch, "c2",
                   "", "Shop A", _FUTURE_DATE, "1 người",
                   "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    # A5 chặn theo TỪNG số -> cho thử số khác (quay lại CONTACT), KHÔNG kết thúc/đặt.
    check(reply.state == S.CONTACT, f"A5: cho thử số khác (CONTACT), đang {reply.state}")
    check(api.created_body is None, "A5: KHÔNG được tạo booking")
    check("024 3826 1301" in reply.reply_text, "A5: đưa số hỗ trợ")


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
    check("024 3826 1301" in reply.reply_text, "handoff: phải đọc số cửa hàng cho khách gọi")


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


def test_modify_party_keeps_addons_course_resets():
    """Đổi COURSE phải xoá add-on (combo cấm khác — BR-09); đổi SỐ NGƯỜI thì KHÔNG, vì add-on
    dùng chung cả nhóm nên không phụ thuộc số người (BR-10, BA cập nhật)."""
    def _booked_group():
        return Session(conversation_id="c", turn_count=1, booking_code="X", editing=False,
                       slots=Slots(shop_id=1, date="d", party_size=2, course_id=3,
                                   addon_ids=[7, 8], addons_decided=True))

    ses = _booked_group()
    sm.apply_modify_target(ses, "party")
    s = ses.slots
    check(s.party_size is None, "đổi số người -> xóa số người")
    check(s.addon_ids == [7, 8] and s.addons_decided is True,
          "đổi số người GIỮ nguyên add-on (dùng chung, không phụ thuộc số người)")

    ses2 = _booked_group()
    sm.apply_modify_target(ses2, "course")
    s2 = ses2.slots
    check(s2.course_id is None, "đổi dịch vụ -> xóa course")
    check(s2.addon_ids == [] and s2.addons_decided is False,
          "đổi dịch vụ -> reset add-on (combo cấm có thể khác, BR-09)")


def test_addon_group_prompt_says_shared():
    """Đặt NHÓM: câu hỏi add-on phải nói rõ áp cho CẢ NHÓM (BR-10, BA cập nhật) và cho biết
    CHỌN ĐƯỢC NHIỀU (web tick nhiều, chat không nói thì khách tưởng chỉ chọn một)."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ses = Ses(conversation_id="c", slots=Sl(party_size=2, course_id=3))
    p = nlg.build_prompt("ADDON", ses, {"addons": []})
    out = nlg.generate(p, _HallucinatingLLM())     # dù có LLM, ADDON vẫn ép template tất định
    check("cả 2 người" in out, "nói rõ add-on áp cho cả nhóm")
    check("Người 1/2" not in out and "Người 2/2" not in out, "không còn hỏi theo từng người")


def test_addon_prompt_reads_list_and_hides_restricted():
    """Câu hỏi add-on phải ĐỌC tên add-on ra (không còn nút), và ẨN add-on bị cấm với
    course đang chọn (BR-09) để không mời nhầm."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ar = {"addons": [
        {"id": 7, "name": "Tinh dầu thơm", "duration_min": 30, "price": 120000,
         "restricted_course_ids": []},
        {"id": 8, "name": "Đá nóng", "duration_min": 15, "price": 90000,
         "restricted_course_ids": [3]},          # cấm với course 3
    ]}
    ses = Ses(conversation_id="c", slots=Sl(party_size=1, course_id=3))
    out = nlg.generate(nlg.build_prompt("ADDON", ses, ar), None)
    check("Tinh dầu thơm" in out, "đọc tên add-on hợp lệ ra cho khách chọn")
    check("Đá nóng" not in out, "add-on bị cấm với course đang chọn KHÔNG được mời (BR-09)")
    check("không" in out.lower(), "có hướng dẫn nói 'không' để bỏ qua")


def test_match_addons_rejects_restricted():
    """Khách đọc tên add-on bị cấm với course -> KHÔNG nhận (BR-09), hỏi lại."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(party_size=1, course_id=3))
    ses.slots.addon_texts = ["Đá nóng"]
    ok = Orchestrator._match_addons(ses, [{"id": 8, "name": "Đá nóng", "duration_min": 15,
                                           "restricted_course_ids": [3]}])
    check(ok is False, "add-on cấm -> không khớp")
    check(ses.slots.addon_ids == [], "không gán add-on cấm cho khách")


def test_modify_party_in_session():
    """Sửa số người 1->2 trong phiên: GIỮ add-on đã chọn, chỉ hỏi lại giờ rồi PATCH (BR-10)."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cmp", *_HAPPY)                       # đặt 1 người xong
    orch.handle_turn("cmp", "sửa lịch")
    orch.handle_turn("cmp", "đổi số người")
    r = orch.handle_turn("cmp", "2 người")
    check(r.state == S.SLOT, f"đổi số người -> giữ add-on, sang chọn giờ luôn, đang {r.state}")
    orch.handle_turn("cmp", "14:15")
    r = orch.handle_turn("cmp", "đồng ý đặt")
    check(api.patched_body is not None, "modify party: phải gọi PATCH")
    check(api.patched_body["party_size"] == 2, "PATCH đúng 2 người")
    check([x["addon_ids"] for x in api.patched_body["reservations"]] == [[], []],
          "PATCH add-on dùng CHUNG cho cả nhóm sau khi sửa số người (BR-10)")
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


class _NoQuestionTypeLLM:
    """Router trả JSON hợp lệ nhưng BỎ field question_type (log thật đã gặp: nó vẫn gán
    intent=ask_info đúng, chỉ thiếu loại câu hỏi)."""
    def complete(self, *a, **k):
        return ('{"intent":"ask_info","entities":{"shop":null,"date":"2026-08-25",'
                '"time":"19:00","party_size":null,"duration":null,"course":null,'
                '"addons":[],"therapist":null,"confirm":null,"location":null}}')


class _OtherQuestionLLM:
    """Router trả JSON hợp lệ nhưng luôn nói đây là câu hỏi ngoài phạm vi."""
    def complete(self, *a, **k):
        return '{"intent":"ask_info","entities":{},"question_type":"other"}'


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
                                    {"reason": "x", "shop_phone": "024 3826 1301"})
    orch = Orchestrator(InMemorySessionStore(), api, None, _settings(support_phone="1900-6068"))
    reply = _drive(orch, "csp", "", "Shop A", _FUTURE_DATE, "1 người",
                   "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    check(reply.state == S.CONTACT, "A5 -> cho thử số khác (CONTACT)")
    check("1900-6068" in reply.reply_text, "hiện số hỗ trợ env")
    check("024 3826 1301" not in reply.reply_text, "không hiện số cửa hàng khi đã có số hỗ trợ env")


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



# --------------------------------------------------------------------------- #
#  Làn QUERY — khách HỎI thông tin (gác cửa + tủ tra cứu)                      #
# --------------------------------------------------------------------------- #
def test_question_does_not_pollute_form():
    """BUG GỐC: hỏi giữa luồng KHÔNG được ghi gì vào tờ đơn, cũng không đổi state.
    Trước đây '7h' rơi vào wanted_time và câu hỏi bị nuốt mất."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cq1", "")
    r0 = orch.handle_turn("cq1", "Shop A")
    check(r0.state == S.DATE, "đang ở bước hỏi ngày")
    r = orch.handle_turn("cq1", "Cửa hàng nào còn mở lúc 7h tối vậy em?")
    s = orch.store.load("cq1").slots
    check(s.date is None, "câu hỏi KHÔNG được điền vào ô ngày")
    check(s.wanted_time is None, "'7h tối' trong câu hỏi KHÔNG được thành giờ mong muốn")
    check(r.state == S.DATE, f"state phải giữ nguyên ở DATE, đang {r.state}")
    check("ngày nào" in r.reply_text, "trả lời xong phải đọc lại câu đang dở")


def test_shops_open_at_answers():
    """Chỉ nêu cửa hàng THỰC SỰ có ca phủ giờ đó (stub: shop 2 làm tới 21:00, shop 1 tới 18:00)."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cq2", "")
    r = orch.handle_turn("cq2", "Cửa hàng nào còn mở lúc 7h tối vậy em?")
    check("Cửa hàng Hải Châu" in r.reply_text, "shop có ca lúc 19:00 phải được nêu")
    check("Shop A" not in r.reply_text, "shop đã hết ca lúc 19:00 KHÔNG được nêu")
    check("giờ trống" not in r.reply_text,
          "chỉ trả lời có cửa hàng nào, không giải thích thêm về giờ trống")


def test_short_answer_stays_in_flow():
    """Chống hồi quy gác cửa: câu trả lời NGẮN vẫn phải đi vào luồng đặt lịch như cũ."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cq3", "", "Shop A")
    check(orch.store.load("cq3").slots.shop_id == 1, "'Shop A' vẫn chọn được cửa hàng")
    orch.handle_turn("cq3", _FUTURE_DATE)
    orch.handle_turn("cq3", "3")
    check(orch.store.load("cq3").slots.party_size == 3, "'3' vẫn là 3 người")
    orch.handle_turn("cq3", "Toàn thân")
    orch.handle_turn("cq3", "không")             # không thêm add-on
    s = orch.store.load("cq3").slots
    check(s.addon_ids == [] and s.addons_decided is True,
          "'không' ở bước add-on vẫn là bỏ qua, không bị hiểu thành câu hỏi")


def test_info_never_uses_llm():
    """Câu trả lời tra cứu chứa giờ/địa chỉ THẬT -> tất định, LLM không được viết lại (§10)."""
    api = StubApi()
    orch = Orchestrator(InMemorySessionStore(), api, _HallucinatingLLM(), _settings())
    orch.handle_turn("cq4", "")
    r = orch.handle_turn("cq4", "Cửa hàng Hải Châu ở đâu vậy em?")
    check("88 Bạch Đằng, Hải Châu, Đà Nẵng" in r.reply_text, "địa chỉ THẬT từ dữ liệu")
    check("0236 3812 1302" in r.reply_text, "số điện thoại THẬT")
    check("0999 999 999" not in r.reply_text, "không để LLM bịa số")


def test_offtopic_three_times_handoff():
    """Hỏi ngoài phạm vi liên tiếp -> không quay vòng, mời gọi cửa hàng."""
    api = StubApi()
    orch = Orchestrator(InMemorySessionStore(), api, _OtherQuestionLLM(), _settings())
    _drive(orch, "cq5", "", "Shop A")            # chọn shop trước để có số mà đọc
    r1 = orch.handle_turn("cq5", "Bên mình có chỗ đỗ xe không ạ?")
    check("chưa hỗ trợ" in r1.reply_text, "lần 1: xin lỗi lịch sự")
    check("ngày nào" in r1.reply_text, "vẫn đọc lại câu đang dở")
    orch.handle_turn("cq5", "Thế có wifi không ạ?")
    r3 = orch.handle_turn("cq5", "Vậy có được mang theo trẻ nhỏ không ạ?")
    check("024 3826 1301" in r3.reply_text, "lần 3: đọc số điện thoại để khách gọi")


def test_shops_near_suggests_shop():
    """Hỏi cửa hàng cùng khu vực -> vừa trả lời vừa điền luôn ô cửa hàng (phiếu đề xuất)."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cq6", "")
    r = orch.handle_turn("cq6", "Nhà tôi ở Đà Nẵng, có cửa hàng nào gần không em?")
    check("Cửa hàng Hải Châu" in r.reply_text, "chỉ ra cửa hàng cùng khu vực")
    check("gần nhất" not in r.reply_text, "không hứa 'gần nhất' — dữ liệu không có toạ độ")
    ses = orch.store.load("cq6")
    check(ses.slots.shop_id == 2, f"điền luôn ô cửa hàng, đang {ses.slots.shop_id}")
    check("ngày nào" in r.reply_text, "chọn được shop -> câu đọc lại nhảy sang hỏi ngày")



def test_question_type_missing_falls_back_to_rules():
    """Router bỏ field question_type -> vẫn phải vào làn hỏi đáp, không tuột về luồng đặt.
    (Bug thật: bot đọc lại danh sách cửa hàng thay vì trả lời, '19:00' chui vào wanted_time.)"""
    api = StubApi()
    orch = Orchestrator(InMemorySessionStore(), api, _NoQuestionTypeLLM(), _settings())
    orch.handle_turn("cq7", "")
    r = orch.handle_turn("cq7", "Cửa hàng nào còn mở lúc 7h tối ?")
    check("Cửa hàng Hải Châu" in r.reply_text, "vẫn trả lời được câu hỏi giờ mở cửa")
    check(orch.store.load("cq7").slots.wanted_time is None,
          "'19:00' trong câu hỏi KHÔNG được thành giờ mong muốn")


def test_course_match_falls_back_to_raw_text():
    """Router XÉ số phút khỏi tên gói ('massage body 30' -> course='massage body' + duration=30)
    làm tên còn lại trúng cả 4 mức -> phải lùi về CÂU GỐC mới chọn đúng. (Bug thật: bot hỏi
    lại đúng cái gói khách vừa đọc.)"""
    courses = [{"id": 8, "name": "Massage body 30"}, {"id": 9, "name": "Massage body 60"},
               {"id": 10, "name": "Massage body 90"}]
    ses = Session(conversation_id="c", turn_count=1)
    ses.history.append({"role": "user", "masked_text": "massage body 30"})
    ses.slots.course_text = "massage body"          # đúng như log thật
    check(Orchestrator._match_course(ses, courses) is True, "phải chọn được gói")
    check(ses.slots.course_id == 8, "chọn ĐÚNG mức 30 phút theo câu gốc")

    # Câu gốc cũng mơ hồ -> vẫn từ chối chọn bừa (giữ nguyên guard cũ).
    ses2 = Session(conversation_id="c", turn_count=1)
    ses2.history.append({"role": "user", "masked_text": "cho tôi massage body"})
    ses2.slots.course_text = "massage body"
    check(Orchestrator._match_course(ses2, courses) is False and ses2.slots.course_id is None,
          "mơ hồ cả ở câu gốc -> hỏi lại, không chọn bừa")


def test_answer_not_mistaken_for_question():
    """NLU gán nhầm question_type cho câu khách TRẢ LỜI ('Hải Châu' -> other, 'Gói đầu tiên'
    -> course_price). Gác cửa phải đòi DẤU HIỆU HỎI, không đếm ký tự (log thật: 'Gói đầu
    tiên' đúng 12 ký tự, sát mép ngưỡng cũ)."""
    orch = _orch(StubApi())
    for text, qt in (("Hải Châu", "other"), ("Gói đầu tiên", "course_price"),
                     ("massage body 30", "course_price")):
        check(orch._is_question({"question_type": qt, "intent": "ask_info"}, text) is False,
              f"{text!r} là câu trả lời, không phải câu hỏi")
    check(orch._is_question({"question_type": "shops_open_at", "intent": "ask_info"},
                            "Cửa hàng nào mở lúc 7h tối nay.") is True,
          "câu hỏi quên dấu '?' vẫn nhận ra nhờ từ để hỏi ('nào')")


def test_shops_open_at_none_open_reads_day_hours():
    """Không cửa hàng nào mở giờ đó -> KHÔNG được nhắc 'giờ trống xem sau' (thừa, khó hiểu
    khi chẳng có cửa hàng nào), phải đọc KHUNG GIỜ trong ngày để khách chọn lại."""
    api = StubApi()                                   # shop1 10:00-18:00, shop2 12:00-21:00
    orch = _orch(api)
    orch.handle_turn("cq8", "")
    r = orch.handle_turn("cq8", "Có cửa hàng nào mở lúc 10h tối không ?")   # 22:00
    check("không cửa hàng nào làm" in r.reply_text, "nói rõ giờ đó không ai làm")
    check("từ 10:00 đến 21:00" in r.reply_text, "đọc khung giờ cả ngày để khách chọn lại")
    check("giờ trống cụ thể" not in r.reply_text,
          "không cửa hàng nào thì đừng nhắc 'giờ trống xem sau'")
    # Giờ QUÁ SỚM cũng phải ra khung giờ (nói mỗi giờ đóng muộn nhất thì lạc đề).
    orch.handle_turn("cq9", "")
    r2 = orch.handle_turn("cq9", "Có cửa hàng nào mở lúc 5h sáng không ?")
    check("từ 10:00 đến 21:00" in r2.reply_text, "hỏi giờ quá sớm vẫn đọc khung giờ")


def test_name_match_tolerates_typo_and_extra_words():
    """Khớp tên hạ dần: chuỗi-con -> theo TỪ ("Sài Gòn đi") -> khớp MỜ ("Massge body 120").
    Mơ hồ vẫn phải từ chối (bug thật: gõ sai 1 chữ là bot hỏi lại đúng thứ vừa mời)."""
    from app import matching
    shops = [{"id": 1, "name": "Cửa hàng Hoàn Kiếm"}, {"id": 2, "name": "Cửa hàng Hải Châu"},
             {"id": 3, "name": "Cửa hàng Sài Gòn"}]
    courses = [{"id": 14, "name": "Massage body 30"}, {"id": 15, "name": "Massage body 60"},
               {"id": 17, "name": "Massage body 120"}, {"id": 18, "name": "Gội đầu dưỡng sinh"}]

    def name(items, q):
        hit = matching.pick_unique(q, items)
        return hit["name"] if hit else None

    check(name(shops, "Sài Gòn đi") == "Cửa hàng Sài Gòn", "khớp theo TỪ, bỏ chữ thừa")
    check(name(shops, "cho tôi Hải Châu nhé") == "Cửa hàng Hải Châu", "chữ thừa hai đầu vẫn khớp")
    check(name(courses, "Massge body 120") == "Massage body 120", "gõ sai 1 chữ vẫn ra đúng mức")
    # Guard cũ phải còn nguyên: mơ hồ thì KHÔNG đoán bừa.
    check(name(shops, "cửa hàng") is None, "'cửa hàng' trúng mọi tên -> hỏi lại")
    check(name(shops, "a") is None and name(shops, "To") is None, "query quá ngắn -> không đoán")
    check(name(courses, "massage body") is None, "thiếu số phút -> mơ hồ giữa 4 mức, hỏi lại")


def test_pick_by_index_selects_from_numbered_list():
    """Danh sách đọc ra có đánh số -> khách gõ số phải chọn được (gói và add-on)."""
    from app import matching
    courses = [{"id": 14, "name": "A"}, {"id": 15, "name": "B"}, {"id": 17, "name": "C"}]
    check(matching.pick_by_index("2", courses)["id"] == 15, "số trần")
    check(matching.pick_by_index("gói 3", courses)["id"] == 17, "có chữ dẫn")
    check(matching.pick_by_index("9", courses) is None, "ngoài phạm vi -> None")
    check(matching.pick_by_index("abc", courses) is None, "không phải số -> None")

    # Add-on: chọn NHIỀU bằng số trong một câu.
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, date="d", party_size=2, course_id=3))
    ses.slots.addon_texts = ["1 với 2"]
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "chọn add-on bằng số")
    check(ses.slots.addon_ids == [7, 8], f"nhận cả hai theo số, đang {ses.slots.addon_ids}")


def test_addon_prompt_numbered_like_courses():
    """Add-on trình bày GIỐNG danh sách gói: mỗi dòng một mục, có số và có GIÁ."""
    from app import nlg
    from app.session import Session as Ses, Slots as Sl
    ar = {"addons": [
        {"id": 7, "name": "Bấm huyệt bàn chân", "duration_min": 15, "price": 80000,
         "restricted_course_ids": []},
        {"id": 8, "name": "Đá nóng", "duration_min": 15, "price": 90000,
         "restricted_course_ids": []},
    ]}
    out = nlg.generate(nlg.build_prompt("ADDON", Ses(conversation_id="c",
                                                    slots=Sl(party_size=1, course_id=3)), ar), None)
    check("1. Bấm huyệt bàn chân · 15 phút · 80.000₫" in out, "dòng 1 có số + thời lượng + giá")
    check("2. Đá nóng · 15 phút · 90.000₫" in out, "dòng 2 đánh số tiếp")
    check("số thứ tự" in out, "nói rõ có thể trả lời bằng số")
    check("chọn nhiều" in out, "nói rõ chọn được nhiều dịch vụ")
    check("tất cả" in out, "nói rõ có thể lấy hết")


def test_change_shop_midflow_clears_shop_catalog():
    """Đòi đổi cửa hàng giữa chừng -> bỏ shop + mọi id thuộc catalog shop cũ (gói/add-on/
    giờ), NHƯNG giữ ngày, số người và liên hệ."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cs", "", "Shop A", _FUTURE_DATE, "2 người", "Toàn thân", "Bấm huyệt")
    before = orch.store.load("cs").slots
    check(before.shop_id == 1 and before.course_id == 3 and before.addon_ids == [7],
          "đã chọn xong shop/gói/add-on")
    r = orch.handle_turn("cs", "Cho tôi đổi cửa hàng khác đi")
    s = orch.store.load("cs").slots
    check(s.shop_id is None and s.course_id is None and s.addon_ids == [],
          "dọn sạch shop + catalog của shop cũ")
    check(s.date == _FUTURE_DATE and s.party_size == 2,
          "GIỮ ngày và số người — chúng không phụ thuộc cửa hàng")
    check(r.state == S.SHOP, f"quay lại bước chọn cửa hàng, đang {r.state}")


def test_fuzzy_match_ignores_common_prefix():
    """Gõ sai tên cửa hàng vẫn khớp. Phần chung "Cửa hàng " có ở MỌI tên nên nó pha loãng
    điểm giống nhau — phải so cả với phần đặc trưng ("hoàn kiêm" vs "hoàn kiếm" gần như
    trùng, trong khi vs cả cụm "cửa hàng hoàn kiếm" thì trượt ngưỡng)."""
    from app import matching
    shops = [{"id": 1, "name": "Cửa hàng Hoàn Kiếm"}, {"id": 2, "name": "Cửa hàng Hải Châu"},
             {"id": 3, "name": "Cửa hàng Sài Gòn"}, {"id": 5, "name": "Cửa hàng ABC"}]
    for typo, want in (("Hoàn Kiêm", 1), ("Hải Chầu", 2), ("Sài Gòng", 3)):
        hit = matching.pick_unique(typo, shops)
        check(hit is not None and hit["id"] == want, f"{typo!r} phải ra shop {want}")
    # Riêng ca này phải do CHÍNH nhánh khớp mờ giải: gõ sai ở cả hai chữ nên không còn
    # từ nào trùng để nhánh khớp-theo-từ đỡ hộ.
    hit = matching._pick_fuzzy("Hoàn Kiêm", shops)
    check(hit is not None and hit["id"] == 1, "khớp mờ phải tự giải được, không nhờ khớp theo từ")
    check(matching.pick_unique("cửa hàng", shops) is None,
          "phần chung vẫn phải mơ hồ, không được khớp mờ bừa")


def test_shops_open_without_time_answers_by_day():
    """"Cửa hàng nào đang mở hôm nay?" là hỏi theo NGÀY — không được hỏi ngược 'khung giờ
    nào ạ?' (né câu hỏi)."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cd1", "")
    r = orch.handle_turn("cd1", "Cửa hàng nào đang mở cửa hôm nay vậy ?")
    check("khung giờ nào" not in r.reply_text, "không được hỏi ngược về giờ")
    check("Shop A" in r.reply_text and "Cửa hàng Hải Châu" in r.reply_text,
          "đọc các cửa hàng có làm trong ngày")


def test_shops_list_question():
    """"Bên mình có các cửa hàng nào?" — câu cơ bản nhất, trước đây rơi vào 'chưa hỗ trợ'."""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cd2", "")
    r = orch.handle_turn("cd2", "Bạn đang có các cửa hàng nào ?")
    check("chưa hỗ trợ" not in r.reply_text, "phải trả lời được")
    check("Shop A" in r.reply_text and "Cửa hàng Hải Châu" in r.reply_text, "đọc đủ cửa hàng")

    # LLM gán question_type=other cho chính câu này -> lưới cứu bằng luật phải bắt lại được.
    orch2 = Orchestrator(InMemorySessionStore(), StubApi(), _OtherQuestionLLM(), _settings())
    orch2.handle_turn("cd3", "")
    r2 = orch2.handle_turn("cd3", "Bạn đang có các cửa hàng nào ?")
    check("Shop A" in r2.reply_text, "qt=other nhưng luật nhận ra -> vẫn trả lời")


def test_shops_by_staff_filters_by_gender_and_count():
    """"Cửa hàng nào có 2 nữ phục vụ?" — lọc theo SỐ nhân viên + GIỚI TÍNH.
    (Stub: Shop A 1 nữ; Cửa hàng Hải Châu 2 nữ + 1 nam.)"""
    api = StubApi()
    orch = _orch(api)
    orch.handle_turn("cst1", "")
    r = orch.handle_turn("cst1", "Cửa hàng nào có 2 nữ phục vụ ?")
    check("Cửa hàng Hải Châu" in r.reply_text, "shop đủ 2 nữ phải được nêu")
    check("Shop A" not in r.reply_text, "shop chỉ 1 nữ KHÔNG được nêu")
    check(orch.store.load("cst1").slots.shop_id == 2, "khớp duy nhất -> điền luôn ô cửa hàng")

    # Không đủ -> nói rõ nơi nhiều nhất, đừng để khách tự mò.
    orch2 = _orch(StubApi())
    orch2.handle_turn("cst2", "")
    r2 = orch2.handle_turn("cst2", "Shop nào có 5 nhân viên nam trực ?")
    check("chưa cửa hàng nào đủ" in r2.reply_text, "báo rõ không đủ")
    check("Cửa hàng Hải Châu với 1 người" in r2.reply_text, "nêu nơi nhiều nhất kèm số thật")


def test_staff_question_not_mistaken_for_handoff():
    """Chữ "nhân viên" xuất hiện ở CẢ hai loại câu. Hỏi thông tin không được đẩy sang
    handoff (bug thật: bot mời gọi điện khi khách hỏi số nhân viên trực)."""
    from app import nlu
    hoi = nlu._rule_based("Cửa hàng nào có 2 nhân viên nam trực ngày 27/8 ?")
    check(hoi["intent"] == "ask_info" and hoi["question_type"] == "shops_by_staff",
          "hỏi số nhân viên -> tra cứu, không phải handoff")
    check(nlu._rule_based("cho tôi gặp nhân viên")["intent"] == "handoff",
          "đòi GẶP người thật thì vẫn phải là handoff")
    check(nlu._rule_based("tôi muốn nói chuyện với người thật")["intent"] == "handoff",
          "cách nói khác của handoff vẫn nhận ra")


def test_request_form_counts_as_question():
    """Khách NHỜ tra cứu thay vì đặt câu hỏi ("tôi muốn tìm cửa hàng có 3 nữ") — không có
    dấu '?' lẫn từ để hỏi, nhưng vẫn là hỏi thông tin."""
    from app import nlu
    check(nlu.looks_like_question("Hiện tại tôi muốn tìm một cửa hàng có 3 nữ phục vụ") is True,
          "dạng đề nghị vẫn tính là hỏi")
    check(nlu.looks_like_question("Shop A") is False, "câu trả lời thường thì không")
    check(nlu.looks_like_question("Massage body 120") is False, "tên gói cũng không")


def test_intent_trail_records_every_turn():
    """Mỗi lượt phải để lại một dấu intent — KỂ CẢ lượt không đi qua NLU (câu chào, menu
    sửa/hủy), nếu không đọc log sẽ thấy khoảng trống không giải thích được."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cit", "", "Shop A", _FUTURE_DATE)
    orch.handle_turn("cit", "Bên mình có những cửa hàng nào ?")
    trail = orch.store.load("cit").intent_trail
    check(trail[0] == "META:chào", f"lượt mở chat vẫn có dấu, đang {trail[:1]}")
    check("ask_info:shops_list" in trail, f"lượt hỏi thông tin ghi cả loại câu hỏi, đang {trail}")
    check(len(trail) == 4, f"đủ 4 lượt, đang {len(trail)}")

    # Sau khi đặt xong, menu sửa/hủy không qua NLU nhưng vẫn phải có dấu.
    orch2 = _orch(StubApi())
    _drive(orch2, "cit2", *_HAPPY)
    orch2.handle_turn("cit2", "sửa lịch")
    orch2.handle_turn("cit2", "đổi giờ")
    check("META:đổi slot" in orch2.store.load("cit2").intent_trail,
          f"nhánh menu sửa cũng ghi dấu, đang {orch2.store.load('cit2').intent_trail}")


def test_confirm_ignored_outside_confirm_step():
    """`confirm` là chốt chặn DUY NHẤT trước POST /bookings, mà NLU hay suy 'yes' từ câu
    chẳng liên quan ("Tôi chọn tất cả nhé"). Lọt một lần là đặt chỗ mà khách chưa thấy
    bản tóm tắt -> chỉ nhận khi bot ĐANG hỏi xác nhận."""
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cc1", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân")
    orch.handle_turn("cc1", "Tôi chọn tất cả nhé")      # ở bước ADDON, NLU suy confirm=yes
    check(orch.store.load("cc1").slots.confirm is None,
          "confirm ngoài bước xác nhận phải bị bỏ")

    # Tới đúng bước CONFIRM thì vẫn nhận bình thường.
    r = _drive(orch, "cc1", "Bấm huyệt", "ai cũng được", "14:00", "0901234567 a@b.com", "đồng ý đặt")
    check(api.created_body is not None, "xác nhận ĐÚNG bước vẫn đặt được")
    check(r.state == S.DONE, f"-> DONE, đang {r.state}")


def test_silent_date_change_is_announced():
    """Khách chỉ nhắc GIỜ ("7h tối nay") mà NLU suy luôn ra ngày -> đơn nhảy sang ngày
    khác. Phải NÓI RA, không thì khách vẫn đinh ninh ngày cũ (bug thật trong log)."""
    from datetime import date, timedelta
    api = StubApi()
    orch = _orch(api)
    _drive(orch, "cd", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không", "ai cũng được")
    r = orch.handle_turn("cd", "Tôi đã chọn 7h tối nay mà")
    today = date.today()
    check("đổi ngày" in r.reply_text, f"phải báo đã đổi ngày, đang: {r.reply_text[:80]}")
    check(f"{today.day}/{today.month}" in r.reply_text, "nêu rõ ngày mới")

    # Lần ĐẦU chọn ngày thì không có gì để "đổi" -> không được báo thừa.
    orch2 = _orch(StubApi())
    orch2.handle_turn("cd2", "")
    orch2.handle_turn("cd2", "Shop A")
    r2 = orch2.handle_turn("cd2", _FUTURE_DATE)
    check("đổi ngày" not in r2.reply_text, "chọn ngày lần đầu không phải là đổi")


def test_shop_with_no_shifts_routes_back_to_shop():
    """Cửa hàng không có ca NGÀY NÀO -> mời đổi CỬA HÀNG. Bảo "chọn ngày khác" là chỉ sai
    đường: khách đổi bao nhiêu ngày cũng vậy (bug thật với Cửa hàng ABC)."""
    api = StubApi()
    api.dead_shops = {2}                               # Cửa hàng Hải Châu không có ca nào
    orch = _orch(api)
    # Đặt xong ngày ở shop sống rồi mới đổi sang shop chết -> ngày CÒN nguyên nên vòng
    # hỏi nhảy thẳng tới COURSE và đụng A1 (đúng như log thật).
    _drive(orch, "cn", "", "Shop A", _FUTURE_DATE, "1 người", "Cho tôi đổi cửa hàng khác")
    r = orch.handle_turn("cn", "Cửa hàng Hải Châu")
    check("chọn ngày khác" not in r.reply_text, "không được bảo đổi ngày")
    check("cửa hàng khác" in r.reply_text, "phải mời đổi cửa hàng")
    check(r.state == S.SHOP and orch.store.load("cn").slots.shop_id is None,
          f"quay lại bước chọn cửa hàng, đang {r.state}")


# --------------------------------------------------------------------------- #
#  FAQ / retrieval (BM25 thuần) — app/retrieval.py                             #
# --------------------------------------------------------------------------- #
_FAQ_CORPUS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "faq.md")


def _settings_faq(**kw):
    """Settings CÓ bật FAQ. Tách khỏi _settings() để 80 test cũ giữ nguyên hành vi
    (corpus rỗng -> làn FAQ tắt hẳn)."""
    base = dict(
        shop_api_base_url="http://x/api/v1",
        llm_base_url="", llm_api_key="", llm_model="m",
        redis_url="", session_ttl_seconds=1800, vault_enc_key="",
        fallback_shop_phone="", support_phone="",
        faq_corpus_path=_FAQ_CORPUS,
    )
    base.update(kw)
    return Settings(**base)


def _orch_faq(api, llm=None):
    return Orchestrator(InMemorySessionStore(), api, llm, _settings_faq())


def test_faq_tokenize_keeps_compound_words():
    """Tách theo khoảng trắng thuần làm vỡ từ ghép tiếng Việt -> BM25 chấm điểm sai.
    Bigram phải có mặt thì 'đặt chỗ' mới là một đơn vị."""
    toks = retrieval.tokenize("Tôi muốn hủy đặt chỗ")
    check("hủy_đặt" in toks, f"phải có bigram 'hủy_đặt', có: {toks}")
    check("hủy" in toks, "vẫn giữ unigram nội dung")
    check("tôi" not in toks, "hư từ 'tôi' phải bị loại")


def test_faq_bm25_prefers_exact_rare_term():
    """Thế mạnh của BM25: thuật ngữ hiếm khớp nguyên văn."""
    chunks = retrieval.load_corpus(_FAQ_CORPUS)
    check(len(chunks) >= 10, f"corpus phải có nhiều mục, đang {len(chunks)}")
    idx = retrieval.BM25Index(chunks)
    hits = idx.search("add-on đặt riêng một mình được không")
    check(hits, "phải có kết quả")
    check("add-on" in chunks[hits[0][0]].title.lower(),
          f"mục đầu phải về add-on, đang {chunks[hits[0][0]].title!r}")


def test_faq_answers_policy_question():
    """Câu hỏi chính sách — không endpoint nào trả — được tra từ data/faq.md."""
    orch = _orch_faq(StubApi())
    _drive(orch, "fq1", "", "Shop A")
    r = orch.handle_turn("fq1", "Tôi muốn hủy lịch thì có mất phí không ạ?")
    check("chưa hỗ trợ" not in r.reply_text, f"không được bó tay: {r.reply_text!r}")
    check("1 tiếng" in r.reply_text, f"phải nêu quy định 1 tiếng (BR-16): {r.reply_text!r}")


def test_faq_answers_group_limit():
    """Thêm câu hỏi mới = thêm mục markdown, không đụng _detect_question — nghiệm thu ý đó."""
    orch = _orch_faq(StubApi())
    _drive(orch, "fq2", "", "Shop A")
    r = orch.handle_turn("fq2", "Bên mình đặt được tối đa mấy người một lần vậy?")
    check("3 người" in r.reply_text, f"phải nêu tối đa 3 người (BR-14): {r.reply_text!r}")


def test_faq_rejects_offtopic_instead_of_guessing():
    """CHỐT QUAN TRỌNG: BM25 trả điểm dương cho bất kỳ chunk nào chung một token, nên
    không có ngưỡng độ phủ thì câu lạc đề vẫn moi ra một mục FAQ và bot trả lời tự tin
    nhưng sai. Thà xin lỗi còn hơn."""
    orch = _orch_faq(StubApi())
    _drive(orch, "fq3", "", "Shop A")
    for q in ("Bên mình có chỗ đỗ xe không ạ?", "Có wifi không ạ?"):
        r = orch.handle_turn("fq3", q)
        check("chưa hỗ trợ" in r.reply_text, f"{q!r} phải bị từ chối, đang: {r.reply_text!r}")


def test_faq_does_not_touch_the_form():
    """Làn QUERY là CHỈ ĐỌC: trả lời xong thì state và tờ đơn phải nguyên vẹn."""
    orch = _orch_faq(StubApi())
    _drive(orch, "fq4", "", "Shop A", _FUTURE_DATE)
    before = asdict(orch.store.load("fq4").slots)
    state_before = orch.store.load("fq4").state
    orch.handle_turn("fq4", "Đổi lịch sau khi đặt có được không ạ?")
    after = orch.store.load("fq4")
    check(asdict(after.slots) == before, "FAQ không được ghi vào tờ đơn")
    check(after.state == state_before, f"state phải giữ nguyên {state_before}, đang {after.state}")


def test_faq_query_is_masked_before_retrieval():
    """Retrieval chạy nội bộ nên PII không bay đi đâu, NHƯNG câu truy vấn vẫn phải được
    mask trước khi tới retriever: SĐT lọt vào token là nó tham gia chấm điểm BM25, và đây
    là chốt giữ nguyên nếu sau này cắm một reranker gọi ra ngoài."""
    seen = []

    class _SpyRetriever:
        chunks = [retrieval.Chunk(id="x", title="t", text="t", tokens=["t"])]

        def search(self, query, top_k=1):
            seen.append(query)
            return []

    orch = _orch_faq(StubApi())
    faq.configure(_SpyRetriever())
    try:
        _drive(orch, "fq5", "", "Shop A")
        orch.handle_turn("fq5", "Số tôi là 0901234567, có chỗ đỗ xe không ạ?")
    finally:
        faq.configure(retrieval.build_retriever(_settings_faq()))
    check(seen, "retriever phải được gọi")
    check("0901234567" not in seen[0], f"SĐT LỌT RA retriever: {seen[0]!r}")
    check("{{phone_" in seen[0], f"phải thấy placeholder: {seen[0]!r}")


def test_faq_off_when_no_corpus():
    """Corpus rỗng -> làn FAQ tắt hẳn, hành vi y như trước khi có module này: LLM gán
    'other' thì bot xin lỗi, KHÔNG lôi văn bản nào ra trả."""
    orch = Orchestrator(InMemorySessionStore(), StubApi(), _OtherQuestionLLM(), _settings())
    _drive(orch, "fq6", "", "Shop A")
    r = orch.handle_turn("fq6", "Tôi muốn hủy lịch thì có mất phí không ạ?")
    check("chưa hỗ trợ" in r.reply_text, f"phải xin lỗi như cũ: {r.reply_text!r}")
    check("1 tiếng" not in r.reply_text, "corpus tắt thì không được trả lời từ FAQ")


def test_faq_does_not_steal_live_data_questions():
    """Câu hỏi có DỮ LIỆU SỐNG (giờ mở cửa, giá) phải tiếp tục đi qua shop_api, không
    được rơi vào văn bản tĩnh — corpus không bao giờ đúng với lịch thay đổi từng ngày."""
    api = StubApi()
    orch = _orch_faq(api)
    _drive(orch, "fq7", "", "Shop A")
    r = orch.handle_turn("fq7", "Cửa hàng nào còn mở lúc 19:00 hôm nay?")
    check("1 tiếng" not in r.reply_text, f"không được trả bằng FAQ: {r.reply_text!r}")
    check("chưa hỗ trợ" not in r.reply_text, f"phải trả lời được qua API: {r.reply_text!r}")



# --------------------------------------------------------------------------- #
#  Hồi quy 5 lỗi tìm ra khi đọc logs/chatbot.log (phiên 26/8)                  #
# --------------------------------------------------------------------------- #
_ORDINAL_COURSES = [{"id": i, "name": n} for i, n in enumerate(
    ["Massage body 30", "Massage body 60", "Massage body 90", "Massage body 120",
     "Gội đầu dưỡng sinh", "Massage tinh dầu 90"], 1)]


def test_yes_word_not_matched_inside_word():
    """Từ đồng ý/hủy phải khớp theo RANH GIỚI TỪ chứ không phải chuỗi con: 'hủy' nằm gọn
    trong 'Thủy' — tên người và tên địa danh (Thủy Nguyên) đều rất phổ biến — nên khớp
    chuỗi con là mọi câu nhắc tới Thủy đều thành intent=cancel. Cùng họ: 'ok' nằm trong
    tên riêng viết không dấu ('Ngọc' -> 'ngoc', 'Khoa'...)."""
    for q in ("Chị Thủy nhé", "Đổi sang Cửa hàng Thủy Nguyên"):
        check(nlu._rule_based(q)["intent"] != "cancel", f"{q!r}: 'Thủy' không phải 'hủy'")
    for q in ("Cho tôi gặp chị Oanh", "Nhân viên tên Khoa"):
        e = nlu._rule_based(q)["entities"]
        check(e["confirm"] is None, f"{q!r} không được thành confirm=yes, đang {e['confirm']!r}")
    # Vẫn phải nhận từ đồng ý thật.
    for q in ("ok", "ok em chốt", "đồng ý", "vâng ạ"):
        check(nlu._rule_based(q)["entities"]["confirm"] == "yes", f"{q!r} phải là confirm=yes")


def test_shop_name_at_confirm_does_not_create_booking():
    """Hậu quả THẬT của lỗi trên: đang ở bước xác nhận mà khách nói "đổi sang Cửa hàng
    Thủy Nguyên" (ý muốn ĐỔI) mà câu bị đọc thành đồng ý/hủy thì booking bị tạo (hoặc bị
    hủy) oan. Chốt `asked != CONFIRM` trong orchestrator KHÔNG cứu được ca này vì đang hỏi
    đúng CONFIRM."""
    api = StubApi()
    orch = _orch(api)                                   # llm=None -> chạy nhánh rule_based
    r = _drive(orch, "tok", "", "Shop A", _FUTURE_DATE, "1 người",
               "Toàn thân", "không", "ai cũng được", "14:00", "0901234567 a@b.com")
    check(r.state == S.CONFIRM, f"phải đang ở CONFIRM, đang {r.state}")
    orch.handle_turn("tok", "Đổi sang Cửa hàng Thủy Nguyên")
    check(api.created_body is None,
          f"KHÔNG được tạo booking từ câu nhắc tên cửa hàng: {api.created_body!r}")


def test_select_all_addons_by_count():
    """"Cả 3 cái" từng rơi xuống nhánh SỐ THỨ TỰ và bị đọc thành "mục số 3": khách xin cả
    ba, hệ thống ghi một, không báo gì (log lượt 7 -> addon_ids=[10])."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, course_id=3, party_size=1))
    ses.slots.addon_texts = ["Cả 2 cái"]                # stub có đúng 2 add-on
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "phải chốt được")
    check(ses.slots.addon_ids == [7, 8], f"phải lấy CẢ HAI, đang {ses.slots.addon_ids}")

    # "cả 2 cái" khi đang mời 3 mục = chọn 2 mục nào đó, KHÔNG phải lấy hết -> không đoán bừa.
    check(nlu.is_select_all("Cả 2 cái", 3) is False, "số không khớp thì không coi là lấy hết")
    check(nlu.is_select_all("tất cả", 3) is True, "'tất cả' luôn là lấy hết")


def test_addon_by_index_keeps_names():
    """Chọn add-on bằng SỐ: nhánh cũ gán thẳng `picked` mà bỏ quên `chosen`, nên add-on có
    id nhưng MẤT TÊN — bản tóm tắt xác nhận hiện thiếu tên dịch vụ khách vừa chọn."""
    ses = Session(conversation_id="c", turn_count=1,
                  slots=Slots(shop_id=1, course_id=3, party_size=1))
    ses.slots.addon_texts = ["số 2"]
    check(Orchestrator._match_addons(ses, _STUB_ADDONS) is True, "phải chốt được")
    check(ses.slots.addon_ids == [8], f"mục số 2 = Đá nóng, đang {ses.slots.addon_ids}")
    check(ses.slots.addon_names == ["Đá nóng"],
          f"tên add-on KHÔNG được rỗng, đang {ses.slots.addon_names!r}")


def test_course_by_spoken_ordinal():
    """"Tôi chọn cái thứ 4" là cách nói số thứ tự tự nhiên nhất, nhưng regex cũ chỉ cho 6
    ký tự đệm trước con số -> trả None -> bot đọc lại danh sách. Log lượt 14+15 cho thấy
    khách nói hai lượt liền vẫn kẹt."""
    for q in ("4", "gói 4", "Cái thú 4", "Tôi chọn cái thứ 4", "cho anh cái thứ 4 nhé"):
        got = matching.pick_by_index(q, _ORDINAL_COURSES)
        check(got is not None and got["id"] == 4, f"{q!r} phải ra mục 4, đang {got}")
    check(matching.pick_by_index("chọn số 2 nhé", _ORDINAL_COURSES)["id"] == 2, "'số 2' -> mục 2")

    # Con số đi kèm ĐƠN VỊ ĐO không phải số thứ tự — nới lỏng regex không được phá chốt này.
    for q in ("gói cho 2 người", "đặt lúc 3 giờ", "gói 60 phút"):
        check(matching.pick_by_index(q, _ORDINAL_COURSES) is None,
              f"{q!r} không phải chọn theo số thứ tự")


def test_therapist_no_preference_phrase():
    """Bot mời "hay để cửa hàng tự sắp?", khách đáp đúng chữ đó, bot hỏi lại y hệt — vòng
    lặp ở log lượt 8. Câu này tới _match_therapist dưới dạng TÊN nên khớp tên thất bại."""
    for q in ("Cửa hàng tự sắp xếp", "để cửa hàng tự sắp", "tùy shop", "sao cũng được"):
        check(nlu.is_no_preference(q) is True, f"{q!r} phải là 'không chỉ định'")
    check(nlu.is_no_preference("Ngọc Ánh") is False, "tên người không phải 'không chỉ định'")

    api = StubApi()
    orch = _orch(api)
    r = _drive(orch, "nopref", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không")
    check(r.state == S.THERAPIST, f"phải đang hỏi nhân viên, đang {r.state}")
    r2 = orch.handle_turn("nopref", "Cửa hàng tự sắp xếp")
    ses = orch.store.load("nopref")
    check(ses.slots.therapist_decided is True, "phải chốt là không chỉ định")
    check(ses.slots.therapist_id is None, "không gán nhầm một người cụ thể")
    check(r2.state != S.THERAPIST, f"không được hỏi lại nhân viên, đang {r2.state}")



# --------------------------------------------------------------------------- #
#  Độ trễ: cắt lượt gọi NLG + timeout tách theo chỗ gọi                        #
# --------------------------------------------------------------------------- #
class _SpyLLM:
    """Đếm lời gọi và ghi lại timeout từng lời. Phân biệt NLU/NLG bằng system prompt."""

    def __init__(self):
        self.nlu_calls, self.nlg_calls = [], []

    def complete(self, system, user, **kw):
        if "trích xuất" in system:                    # _NLU_SYSTEM
            self.nlu_calls.append(kw.get("timeout"))
            return '{"intent":"book","entities":{},"question_type":null}'
        self.nlg_calls.append((kw.get("timeout"), user))
        return "Câu do LLM viết."


def test_data_bearing_states_never_call_llm():
    """Cả một phiên đặt lịch đầy đủ chỉ được gọi LLM cho NLG ĐÚNG MỘT LẦN (lời chào).

    Log 26/8: 11/15 lượt gọi LLM để sinh câu, mỗi lượt 1,9–3,4s — bằng nửa độ trễ phiên.
    Mấy câu đó chỉ đọc lại danh sách lấy từ API nên template làm được, và làm chính xác hơn."""
    spy = _SpyLLM()
    orch = Orchestrator(InMemorySessionStore(), StubApi(), spy, _settings())
    _drive(orch, "lat", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không",
           "ai cũng được", "14:00", "0901234567 a@b.com")

    keys = [u for _, u in spy.nlg_calls]
    check(len(spy.nlg_calls) <= 1, f"NLG chỉ được gọi LLM tối đa 1 lần, đang {len(keys)}: {keys}")
    check(len(spy.nlu_calls) >= 8, f"NLU vẫn phải chạy mỗi lượt, đang {len(spy.nlu_calls)}")


def test_llm_timeout_is_per_call_site():
    """NLU chặn cả lượt chat (khách ngồi đợi) nên hạn ngắn hơn; NLG chỉ còn GREETING và
    hỏng thì có câu mẫu nên ngắn hơn nữa. Trước đây dùng chung 20s cho cả hai."""
    st = _settings()
    check(st.llm_timeout_nlu < 20 and st.llm_timeout_nlg < st.llm_timeout_nlu,
          f"nlu={st.llm_timeout_nlu} nlg={st.llm_timeout_nlg}")

    spy = _SpyLLM()
    orch = Orchestrator(InMemorySessionStore(), StubApi(), spy, st)
    orch.handle_turn("to", "chào em")
    check(spy.nlu_calls and spy.nlu_calls[0] == st.llm_timeout_nlu,
          f"NLU phải nhận timeout={st.llm_timeout_nlu}, đang {spy.nlu_calls}")
    if spy.nlg_calls:
        check(spy.nlg_calls[0][0] == st.llm_timeout_nlg,
              f"NLG phải nhận timeout={st.llm_timeout_nlg}, đang {spy.nlg_calls[0][0]}")


def test_confirm_summary_is_deterministic():
    """CONFIRM là màn khách dựa vào để đồng ý — nội dung phải khớp từng chữ với đơn sắp
    gửi đi, không phải bản LLM diễn đạt lại."""
    api = StubApi()
    orch = Orchestrator(InMemorySessionStore(), api, _HallucinatingLLM(), _settings())
    r = _drive(orch, "cfd", "", "Shop A", _FUTURE_DATE, "1 người", "Toàn thân", "không",
               "ai cũng được", "14:00", "0901234567 a@b.com")
    check(r.state == S.CONFIRM, f"phải ở CONFIRM, đang {r.state}")
    check("0999 999 999" not in r.reply_text, "LLM không được viết lại bản tóm tắt")
    check("Shop A" in r.reply_text and "14:00" in r.reply_text,
          f"tóm tắt vẫn đủ thông tin thật: {r.reply_text!r}")



def _m(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


class _ShiftAwareApi(StubApi):
    """/slots sinh ĐÚNG theo ca làm: lượt phục vụ phải XONG trước giờ nhân viên tan ca và
    cần đủ ngần ấy người cùng lúc.

    StubApi gốc trả danh sách giờ cứng nên không kiểm được ràng buộc "gói dài hơn phần ca
    còn lại" — đúng chỗ sinh ra bug trong log (bot kể tên cửa hàng mở lúc 19:00 trong khi
    gói 135 phút của khách 21:15 mới xong)."""

    _DUR = {3: 60, 4: 120}          # course_id -> phút

    def get_services(self, shop_id, date, party_size=None):
        data = super().get_services(shop_id, date, party_size)
        if not data["courses"]:
            return data
        return dict(data, courses=list(data["courses"]) + [
            {"id": 4, "name": "Thư giãn sâu", "duration_min": 120, "price": 9000}])

    def get_slots(self, shop_id, **kw):
        self.calls.append("slots")
        self.last_slots_kw = kw
        need = self._DUR.get(kw["course_id"], 60) + 15 * len(kw.get("addon_ids") or [])
        party = kw.get("party_size") or 1
        shifts = [sh for th in self.get_timeline(shop_id, kw["date"])["therapists"]
                  for sh in th["shifts"]]
        if len(shifts) < party:
            return {"slots": []}
        t = min(_m(sh["start_time"]) for sh in shifts)
        # Nhóm `party` người cần `party` nhân viên cùng lúc -> giờ tan ca thứ party.
        last = sorted((_m(sh["end_time"]) for sh in shifts), reverse=True)[party - 1]
        out = []
        while t + need <= last:
            out.append(f"{t // 60:02d}:{t % 60:02d}")
            t += 15
        return {"slots": out}


def test_shops_open_at_fits_combo_in_shift():
    """Đã chốt gói -> "cửa hàng nào mở lúc 19h?" phải hiểu là "chỗ nào NHẬN ĐƯỢC gói này
    lúc 19h": gói 60 phút bắt đầu 19:00 thì 20:00 xong, vẫn trong ca tới 21:00 của Hải Châu;
    Shop A tan ca 18:00 nên không được nêu."""
    api = _ShiftAwareApi()
    orch = _orch(api)
    _drive(orch, "cfit", "", "Cửa hàng Hải Châu", _FUTURE_DATE, "1 người", "Toàn thân", "không")
    r = orch.handle_turn("cfit", "Cửa hàng nào còn mở lúc 7h tối ?")
    check("Cửa hàng Hải Châu" in r.reply_text, f"shop nhận được gói lúc 19:00: {r.reply_text!r}")
    check("Shop A" not in r.reply_text, "shop đã tan ca lúc 19:00 KHÔNG được nêu")
    check("20:00" in r.reply_text, f"phải nói lượt xong lúc mấy giờ: {r.reply_text!r}")


def test_shops_open_at_rejects_combo_past_shift_end():
    """Bug thật trong log: bước chọn giờ báo "19:00 không còn trống", lượt sau bot lại kể
    tên chính cửa hàng đó là "mở lúc 19:00". Gói 150 phút bắt đầu 19:00 thì 21:30 mới xong,
    muộn hơn giờ tan ca 21:00 -> phải NÓI RÕ vướng ở đâu và gợi ý gói vừa ca làm."""
    api = _ShiftAwareApi()
    orch = _orch(api)
    _drive(orch, "clate", "", "Cửa hàng Hải Châu", _FUTURE_DATE, "1 người", "Thư giãn sâu",
           "tất cả")
    ses = orch.store.load("clate")
    check(ses.slots.course_id == 4 and len(ses.slots.addon_ids) == 2,
          f"chuẩn bị đơn: gói 120 phút + 2 add-on, đang {ses.slots.course_id}/{ses.slots.addon_ids}")

    r = orch.handle_turn("clate", "Cửa hàng nào còn mở lúc 7h tối ?")
    check("chưa cửa hàng nào nhận được" in r.reply_text,
          f"không được hứa suông là có shop mở: {r.reply_text!r}")
    check("21:00" in r.reply_text, f"nói giờ nhân viên tan ca: {r.reply_text!r}")
    check("150 phút" in r.reply_text and "21:30" in r.reply_text,
          f"nói gói dài bao nhiêu và mấy giờ mới xong: {r.reply_text!r}")
    check("20:30" in r.reply_text,
          f"gợi ý gói ngắn hơn, xong TRƯỚC giờ tan ca: {r.reply_text!r}")
    check("18:30" in r.reply_text,
          f"giữ nguyên gói thì nói giờ bắt đầu muộn nhất: {r.reply_text!r}")


def test_shops_open_at_needs_enough_staff_for_group():
    """Nhóm 3 người cần 3 nhân viên CÙNG LÚC. Shop A chỉ có 1 người trực -> lúc 19:00 nói
    rõ thiếu người, không đổ cho "hết giờ"."""
    api = _ShiftAwareApi()
    api.dead_shops = {2}                       # chỉ còn Shop A (10:00-18:00, 1 nhân viên)
    orch = _orch(api)
    _drive(orch, "cgrp", "", "Shop A", _FUTURE_DATE, "3 người", "Toàn thân", "không")
    r = orch.handle_turn("cgrp", "Cửa hàng nào còn mở lúc 2h chiều ?")
    check("1 nhân viên cùng trực" in r.reply_text and "3 người" in r.reply_text,
          f"nói rõ thiếu người chứ không phải hết giờ: {r.reply_text!r}")


def test_same_shop_mention_keeps_order():
    """Nhắc lại ĐÚNG cửa hàng đang chọn không phải là đổi cửa hàng. Bug thật trong log:
    "Tôi chọn Hải Châu lúc 7h tối với dịch vụ tôi đã chọn" làm bay sạch gói + add-on."""
    ses = Session(conversation_id="c")
    ses.slots.shop_id = 2
    ses.slots.shop_name = "Cửa hàng Hải Châu"
    ses.slots.course_id = 13
    ses.slots.course_name = "Massage tinh dầu 90"
    ses.slots.addon_ids = [7, 8]
    ses.slots.addon_names = ["Bấm huyệt bàn chân", "Đá nóng"]
    ses.slots.addons_decided = True

    sm.merge_params(ses, {"shop": "Hải Châu", "time": "19:00"})
    check(ses.slots.shop_id == 2, "vẫn là cửa hàng cũ, không phải bỏ ra chọn lại")
    check(ses.slots.course_id == 13 and ses.slots.course_name == "Massage tinh dầu 90",
          "gói khách đã chọn phải còn nguyên")
    check(ses.slots.addon_ids == [7, 8] and ses.slots.addons_decided is True,
          "add-on đã chốt phải còn nguyên")
    check(ses.slots.wanted_time == "19:00", "giờ mong muốn vẫn ghi nhận")

    # Đổi sang cửa hàng KHÁC thì vẫn phải xóa hết như cũ (id catalog không dùng chung).
    sm.merge_params(ses, {"shop": "Sài Gòn"})
    check(ses.slots.shop_id is None and ses.slots.course_id is None
          and ses.slots.addon_ids == [], "đổi sang shop khác vẫn xóa catalog cũ")


def run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{_PASSED} checks passed across {len(tests)} tests.")


if __name__ == "__main__":
    run_all()
