"""Dialog Orchestrator — vòng xử lý 1 lượt (chatbot-architecture.md §1, DD §3.1).

LLM CHỈ ở ①(NLU) và ⑥(NLG). Bước ②③④⑤ là code thuần (test không cần LLM — §9).
PII mask bao quanh ①⑥.

KHÔNG còn nút bấm: khách chỉ trả lời bằng lời, nên mọi lựa chọn đi qua NLU rồi được code
map về id (._match_*). Vài câu trả lời chỉ hiểu được theo NGỮ CẢNH state (số trần ở bước
DATE, "không" ở bước ADDON, "đổi giờ" khi đang sửa lịch) — xử lý ngay trong handle_turn.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app import nlg, nlu, pii
from app import state_machine as sm
from app import states as S
from app.config import Settings
from app.llm_client import RealLLMClient
from app.session import Session, SessionStore
from app.shop_api_client import ShopApiClient, ShopApiError


# Bước mà câu trả lời của khách là TÊN lấy từ dữ liệu (shop/course/add-on/nhân viên), không
# phải câu nói bình thường -> không dùng để đoán ngôn ngữ.
_LANG_NEUTRAL_STATES = {S.SHOP, S.COURSE, S.ADDON, S.THERAPIST}


@dataclass
class BotReply:
    conversation_id: str
    reply_text: str
    state: str
    # Giữ `ui.buttons` (luôn rỗng) để widget FE hiện tại đọc `reply.ui.buttons` không vỡ —
    # bỏ hẳn field sẽ làm FE ném TypeError. Bot đọc lựa chọn thẳng trong reply_text.
    ui: dict = field(default_factory=lambda: {"buttons": []})
    done: bool = False


class Orchestrator:
    # Số ngày dò tới trước để biết cửa hàng THỰC SỰ có ca làm ngày nào (bước DATE).
    _AVAIL_HORIZON_DAYS = 14

    def __init__(self, store: SessionStore, api: ShopApiClient,
                 llm: RealLLMClient | None, settings: Settings):
        self.store = store
        self.api = api
        self.llm = llm
        self.settings = settings
        self._shops_cache: tuple[float, list[dict]] | None = None
        # shop_id -> (epoch, list[ISO ngày mở cửa). Cache 5' để khỏi dò lại mỗi lượt.
        self._avail_cache: dict[int, tuple[float, list[str]]] = {}

    # ------------------------------------------------------------------ #
    #  Vòng 1 lượt                                                        #
    # ------------------------------------------------------------------ #
    def handle_turn(self, conversation_id: str | None, user_text: str,
                    lang_hint: str | None = None) -> BotReply:
        cid = conversation_id or str(uuid.uuid4())
        session = self.store.load(cid) or Session(conversation_id=cid)
        session.turn_count += 1
        if lang_hint:
            session.lang = lang_hint
        if self.settings.force_lang:          # FORCE_LANG: chốt cứng, bỏ mọi cơ chế tự đoán
            session.lang = self.settings.force_lang
            session.lang_locked = True

        # Mở chat (text rỗng) -> câu chào.
        if not (user_text or "").strip():
            return self._greeting(session)

        # Đang chờ khách NHẬP LẠI EMAIL để xác thực sửa/hủy sau cửa sổ 2' (BR-15) — dòng này
        # là email khách gõ, không phải câu đặt lịch: nạp email rồi làm nốt thao tác đang chờ.
        if session.awaiting_edit_email:
            return self._resume_with_edit_email(session, user_text)

        # State bot ĐANG hỏi — suy từ slots nên đúng cả khi state đã lưu chưa kịp cập nhật
        # (vd ngay sau câu chào). Cần để hiểu những câu chỉ có nghĩa trong ngữ cảnh câu hỏi:
        # "31" = ngày, "Shop A" = tên cửa hàng, "không" = không thêm add-on.
        asked = sm.next_state(session)

        # Khách CHỌN ngôn ngữ bằng LỜI ("Tiếng Việt"/"English"). Bỏ qua NLU: cả câu chỉ là
        # tên ngôn ngữ, không có tham số gì để trích (khỏi tốn một lượt gọi LLM vô ích).
        # FORCE_LANG bật thì kể cả yêu cầu tường minh cũng không đổi (đúng nghĩa "cố định").
        if not self.settings.force_lang and \
                (lang_choice := nlu.detect_lang_choice(user_text)) is not None:
            session.lang = lang_choice
            session.lang_locked = True
        # Đang ở menu "đổi gì" (UC-02): câu này là chọn phần muốn đổi, không phải câu đặt mới.
        elif session.editing and nlu.is_cancel_request(user_text):
            return self._cancel(session)
        elif session.editing and (target := nlu.detect_modify_target(user_text)) is not None:
            sm.apply_modify_target(session, target)
        else:
            # ① NLU (LLM) — mask PII trước khi ra LLM (bước ⑥.1 của masker).
            extra = [session.booking_code] if session.booking_code else None
            masked = pii.mask(user_text, session.vault, extra_values=extra)
            session.history.append({"role": "user", "masked_text": masked})

            parsed = nlu.extract(masked, session.lang, self.llm)
            if parsed is None:                       # sai schema -> hỏi lại (§3.4)
                return self._reply(session, "REPROMPT", {})

            # Đã chọn ngôn ngữ thì tôn trọng, không đoán. Cũng KHÔNG đoán ở các bước chọn từ
            # danh sách: câu trả lời khi đó là TÊN dữ liệu của mình ("Shop A", "Foot") —
            # toàn chữ Latin nhưng không phải tín hiệu tiếng Anh, đoán theo sẽ làm bot lật
            # qua lại vi/en giữa chừng hội thoại.
            if not session.lang_locked and asked not in _LANG_NEUTRAL_STATES:
                lang = nlu.detect_lang(user_text)
                if lang:
                    # CHỐT luôn cho cả phiên: đoán đi đoán lại mỗi lượt là nguồn gốc việc bot
                    # đang nói tiếng Việt bỗng nhảy sang tiếng Anh giữa chừng. Khách muốn đổi
                    # thì nói thẳng "English"/"Tiếng Việt" (detect_lang_choice ở trên).
                    session.lang = lang
                    session.lang_locked = True
            if parsed["intent"] == "handoff":
                return self._handoff(session)

            # Đã đặt xong mà khách nhắn tiếp: sửa/hủy bằng lời (UC-02/03).
            if session.booking_code:
                if parsed["intent"] == "cancel":
                    return self._cancel(session)
                if parsed["intent"] == "modify" and not session.editing:
                    session.editing = True
                    return self._reply(session, S.MODIFY, {})
                if parsed["intent"] == "book" and not session.editing:
                    session.editing = True             # đổi field bằng lời -> vào chế độ sửa
                    session.slots.confirm = None

            self._capture_contact_from_vault(session)  # phone/email từ vault (Q6 lưới hứng)
            # ② MERGE
            sm.merge_params(session, parsed["entities"])

            # Đang hỏi NGÀY mà khách trả lời số trần ("31") hay "31/7" — NLU stateless bỏ số
            # trần để khỏi nhầm với số người; ở đây có ngữ cảnh nên diễn giải thành ngày.
            if asked == S.DATE and session.slots.date is None:
                iso = nlu.parse_date_freeform(user_text, allow_bare_day=True)
                if iso:
                    sm.merge_params(session, {"date": iso})

            # Đang hỏi ADD-ON mà khách nói "không"/"thôi" -> người này không thêm, sang người
            # kế (BR-10). Chỉ hiểu được theo ngữ cảnh: cùng chữ "không" ở bước CONFIRM lại
            # mang nghĩa từ chối đơn.
            if asked == S.ADDON and nlu.is_negative(user_text):
                sm.skip_addon_guest(session)
            else:
                self._capture_choice_text(session, asked, user_text)

        # Nhóm >3 -> handoff (BR-14 / A8), không cần gọi BE.
        if session.slots.party_over:
            return self._handoff(session, reason_party=True)

        # ③ STATE MACHINE
        session.state = sm.next_state(session)
        # ④ VALIDATE + CALL API (có thể đổi session.state theo A1/A2/lỗi)
        api_result = self._run_state_action(session)

        # ⑤⑥ NLG
        render_key = api_result.get("render_key", session.state)
        return self._reply(session, render_key, api_result)

    # ------------------------------------------------------------------ #
    #  Bước ④ — hành động theo state                                      #
    # ------------------------------------------------------------------ #
    def _run_state_action(self, session: Session) -> dict:
        st = session.state
        s = session.slots
        try:
            if st == S.SHOP:
                shops = self._get_shops()
                if self._match_shop(session, shops):
                    # Khách đã nêu tên cửa hàng ("Sendai") -> map xong thì khỏi hỏi lại,
                    # tiến thẳng sang state kế (khách nói tên là đủ, không phải chọn lại).
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"shops": pii.mask_response(shops)}

            if st == S.DATE:
                # Chỉ mời ngày cửa hàng THỰC SỰ có ca. Không dò được (API lỗi) -> active=None,
                # câu hỏi lùi về hỏi chung chung, không đọc danh sách ngày.
                active = self._available_dates(s.shop_id)
                if active is not None and not active:         # shop không có ca suốt horizon
                    s.shop_id = None                          # -> mời chọn cửa hàng khác
                    session.state = S.SHOP
                    return {"render_key": "ERROR",
                            "message": "Cửa hàng này hiện chưa có lịch làm việc trong thời gian "
                                       "tới. Anh/chị chọn giúp cửa hàng khác nhé.",
                            "shops": pii.mask_response(self._get_shops())}
                return {"active_dates": active}

            if st == S.COURSE:
                data = self.api.get_services(s.shop_id, s.date, s.party_size)
                if data.get("reason") == "SHOP_CLOSED":           # A1 (200 rỗng, không phải lỗi)
                    from datetime import date as _date, timedelta as _td

                    s.date = None
                    session.state = S.DATE
                    active = self._available_dates(s.shop_id)
                    # Không còn nút -> ĐỌC thẳng các ngày có làm trong 7 ngày tới cho khách
                    # chọn lại ngay; 7 ngày tới không có thì đọc các ngày xa hơn còn dò được.
                    horizon = (_date.today() + _td(days=7)).isoformat()
                    week = nlg.format_date_list([d for d in (active or []) if d <= horizon])
                    if week:
                        msg = ("Cửa hàng không phục vụ ngày này. Trong 7 ngày tới cửa hàng "
                               f"có làm các ngày: {week}. Anh/chị chọn giúp một ngày nhé.")
                    elif active:
                        msg = ("Cửa hàng không phục vụ ngày này. Cửa hàng có làm các ngày: "
                               f"{nlg.format_date_list(active)}. Anh/chị chọn giúp một ngày nhé.")
                    else:
                        msg = "Cửa hàng không phục vụ ngày này. Mời anh/chị chọn ngày khác."
                    return {"render_key": "ERROR", "message": msg, "active_dates": active}
                courses = data.get("courses", [])
                matched = self._match_course(session, courses)
                self._cache_course(session, courses)
                if matched:
                    # Khách đọc tên gói ("Toàn thân") -> chốt luôn, khỏi hỏi lại.
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"courses": courses}

            if st == S.ADDON:                                     # bước RIÊNG: add-on từng người
                s.ensure_guest_addons()                           # BR-10: mỗi người 1 danh sách
                data = self.api.get_services(s.shop_id, s.date, s.party_size)
                self._cache_course(session, data.get("courses", []))
                addons = data.get("addons", [])
                if self._match_addons(session, addons):
                    # Khách đã đọc tên add-on -> gán cho người đang hỏi rồi sang người kế,
                    # khỏi hỏi lại (không có nút "✅ Xong" để bấm nữa).
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"addons": addons}

            if st == S.SLOT:
                data = self.api.get_slots(
                    s.shop_id, date=s.date, party_size=s.party_size, course_id=s.course_id,
                    # GET /slots chỉ nhận MỘT bộ add-on -> gửi HỢP của mọi người (giờ trống ước
                    # lượng thận trọng); BE re-check add-on RIÊNG từng reservation lúc tạo.
                    addon_ids=self._union_addons(session), therapist_id=s.therapist_id,
                    therapist_gender=s.therapist_gender,
                )
                slots = self._future_slots(data.get("slots", []), s.date)  # bỏ giờ đã qua (hôm nay)
                if not slots:                                     # A2 (200 {slots:[]}) hoặc hết giờ
                    if s.therapist_id or s.therapist_gender:      # do nhân viên chỉ định kín lịch
                        session.state = S.THERAPIST
                        s.therapist_decided = False
                        return {"render_key": "ERROR",
                                "message": "Nhân viên anh/chị chọn đã kín lịch ngày này. "
                                           "Anh/chị đổi người khác, để cửa hàng sắp giúp, hay đổi ngày ạ?"}
                    s.date = None                                 # cả ngày hết khung giờ trống
                    session.state = S.DATE
                    return {"render_key": "ERROR",
                            "message": "Ngày này không còn khung giờ trống, anh/chị chọn giúp ngày khác nhé."}
                # Khách đã NÓI đúng một giờ còn trống -> chốt luôn, khỏi hỏi lại (thay cho
                # nút giờ trước đây). Giờ khách nói mà kín lịch thì rơi xuống mời chọn lại.
                if s.wanted_time and s.wanted_time in slots:
                    s.slot = s.wanted_time
                    s.wanted_time = None
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"slots": self._order_slots(slots, s.wanted_time)}

            if st == S.THERAPIST:
                data = self.api.get_therapists(s.shop_id, s.date)
                therapists = data.get("therapists", [])
                if self._match_therapist(session, therapists):
                    # Khách đã nêu tên nhân viên ("Hana") -> map xong thì khỏi hỏi lại,
                    # tiến thẳng sang SLOT (lọc giờ theo đúng người đó).
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"therapists": therapists}

            if st == S.CONTACT:
                if not (s.phone and s.email):
                    return {}                                     # chưa đủ -> hỏi phone/email
                if not s.contact_verified:
                    real_phone = pii.unmask_value(s.phone, session.vault)
                    info = self.api.lookup_customer(real_phone)   # có thể ném PHONE_BLOCKED (A5)
                    s.contact_verified = True
                    # Đã chặn NG xong -> tiến tiếp (CONFIRM, hoặc CREATE nếu đã đồng ý).
                    session.state = sm.next_state(session)
                    if session.state == S.CREATE:
                        return self._create_booking(session)
                    return {"customer": pii.mask_response(info)}
                return {}

            if st == S.CREATE:
                return self._create_booking(session)

            if st == S.UPDATE:
                return self._update_booking(session)

        except ShopApiError as e:
            return self._map_error(session, e)
        return {}

    # ------------------------------------------------------------------ #
    #  CREATE — POST /bookings (§3.5)                                     #
    # ------------------------------------------------------------------ #
    def _create_booking(self, session: Session) -> dict:
        s = session.slots
        # Guardrail 1 (§4.2): chỉ ghi khi đã CONFIRM đồng ý.
        if s.confirm != "yes":
            session.state = S.CONFIRM
            return {}

        # Chốt chặn: PII phải giải được THẬT. Nếu placeholder mất (vault rút giữa chừng) ->
        # đừng gửi "{{email_1}}" cho BE (400 VALIDATION_ERROR -> REPROMPT khó hiểu) mà xin lại.
        phone = pii.unmask_value(s.phone, session.vault)
        email = pii.unmask_value(s.email, session.vault)
        if not self._pii_resolved(phone) or not self._pii_resolved(email):
            if not self._pii_resolved(phone):
                s.phone = None
            if not self._pii_resolved(email):
                s.email = None
            s.contact_verified = False
            session.state = S.CONTACT
            return {}

        s.ensure_guest_addons()                                   # BR-10: add-on RIÊNG từng người
        reservations = [{"addon_ids": list(s.guest_addons[i])}
                        for i in range(s.party_size or 1)]
        body = {
            "shop_id": s.shop_id,
            "date": s.date,
            "start_time": s.slot,
            "party_size": s.party_size,
            "phone": phone,
            "email": email,
            "course_id": s.course_id,
            "reservations": reservations,
            "therapist_id": s.therapist_id,
            "therapist_gender": s.therapist_gender,
        }
        try:
            resp = self.api.create_booking(body)
        except ShopApiError as e:
            return self._map_error(session, e)

        session.booking_code = resp.get("booking_code")
        session.edit_token = resp.get("edit_token")
        session.edit_token_expires_at = time.time() + resp.get("edit_token_expires_in", 120)
        session.editing = False
        session.state = S.DONE
        return {}

    # ------------------------------------------------------------------ #
    #  UPDATE — PATCH /bookings/{code} (UC-02, sửa trong phiên)           #
    # ------------------------------------------------------------------ #
    def _update_booking(self, session: Session) -> dict:
        s = session.slots
        if s.confirm != "yes":                                     # đọc lại đơn rồi mới ghi
            session.state = S.CONFIRM
            return {}

        s.ensure_guest_addons()                                    # BR-10: add-on RIÊNG từng người
        reservations = [{"addon_ids": list(s.guest_addons[i])}
                        for i in range(s.party_size or 1)]
        body = {
            "date": s.date, "start_time": s.slot, "party_size": s.party_size,
            "course_id": s.course_id, "reservations": reservations,
            "therapist_id": s.therapist_id, "therapist_gender": s.therapist_gender,
        }
        code = session.booking_code
        now = time.time()
        token_alive = bool(
            session.edit_token and session.edit_token_expires_at
            and now < session.edit_token_expires_at
        )
        try:
            if token_alive:
                self.api.patch_booking(code, body, edit_token=session.edit_token)  # BR-17
            else:
                real_email = pii.unmask_value(s.email, session.vault)
                if not self._pii_resolved(real_email):             # vault đã rút -> xin LẠI email
                    session.awaiting_edit_email = True              # (BR-15) rồi PATCH tiếp
                    session.edit_email_for = "update"
                    return {"render_key": "ERROR",
                            "message": f"Cửa sổ sửa nhanh 2 phút đã hết. Anh/chị nhập lại email "
                                       f"đã đặt (mã {code}) để em xác thực và cập nhật lịch giúp ạ."}
                body["email"] = real_email                         # BR-15
                self.api.patch_booking(code, body, edit_token=None)
        except ShopApiError as e:
            return self._map_error(session, e)

        session.editing = False
        session.awaiting_edit_email = False
        session.edit_email_for = ""
        session.state = S.DONE
        return {"render_key": "UPDATED"}

    # ------------------------------------------------------------------ #
    #  CANCEL — POST /bookings/{code}/cancel (UC-03)                      #
    # ------------------------------------------------------------------ #
    def _cancel(self, session: Session) -> BotReply:
        s = session.slots
        code = session.booking_code
        if not code:
            return self._reply(session, "ERROR", {"message": "Hiện chưa có lịch nào để hủy ạ."})
        real_email = pii.unmask_value(s.email, session.vault)
        if not self._pii_resolved(real_email):                     # vault đã rút -> xin LẠI email
            session.awaiting_edit_email = True                     # (BR-15) rồi hủy tiếp
            session.edit_email_for = "cancel"
            return self._reply(session, "ERROR", {
                "message": f"Cửa sổ hủy nhanh 2 phút đã hết. Anh/chị nhập lại email đã đặt "
                           f"(mã {code}) để em xác thực và hủy lịch giúp ạ."})
        try:
            self.api.cancel_booking(code, real_email)              # cancel cần email (BR-15)
        except ShopApiError as e:
            res = self._map_error(session, e)
            return self._reply(session, res.get("render_key", session.state), res)

        session.awaiting_edit_email = False
        session.edit_email_for = ""
        session.state = S.CANCELLED
        return self._reply(session, "CANCELLED", {})

    # ------------------------------------------------------------------ #
    #  Nhập lại email để xác thực sửa/hủy sau cửa sổ 2' (BR-15)           #
    # ------------------------------------------------------------------ #
    def _resume_with_edit_email(self, session: Session, user_text: str) -> BotReply:
        """Khách gõ email để xác thực -> nạp vào vault rồi làm nốt update/cancel đang chờ.
        Email SAI -> BE trả BOOKING_NOT_FOUND -> _map_error gỡ email sai + re-arm để xin lại."""
        pii.mask(user_text, session.vault)                          # side effect: nạp email vào vault
        email_ph = next((k for k in session.vault if k.startswith("{{email_")), None)
        if not email_ph:                                            # gõ chưa ra email hợp lệ -> vẫn chờ, hỏi lại
            return self._reply(session, "ERROR", {
                "message": "Em chưa nhận được email hợp lệ. Anh/chị nhập lại email đã đặt lịch giúp em ạ."})

        session.slots.email = email_ph
        session.awaiting_edit_email = False        # tiêu thụ lượt này; email sai sẽ được re-arm
        # KHÔNG xóa edit_email_for ở đây — nếu email sai còn biết đang chờ update hay cancel.

        if session.edit_email_for == "cancel":
            return self._cancel(session)
        session.slots.confirm = "yes"                              # thay đổi đã xác nhận từ trước
        session.state = S.UPDATE
        res = self._update_booking(session)
        return self._reply(session, res.get("render_key", session.state), res)

    # ------------------------------------------------------------------ #
    #  Map error.code -> nhánh state (§3.6)                               #
    # ------------------------------------------------------------------ #
    def _map_error(self, session: Session, e: ShopApiError) -> dict:
        code = e.code
        d = e.details or {}
        s = session.slots

        if code == "SLOT_CONFLICT":                                # A6
            session.state = S.SLOT
            s.slot = None
            s.confirm = None
            return {"suggested_slots": d.get("suggested_slots", [])}

        if code == "PHONE_BLOCKED":                                # A5
            # Chặn theo TỪNG SĐT -> cho khách thử SỐ KHÁC thay vì kết thúc hẳn (trước đây vào
            # END: state terminal khiến maybe_drop_vault XÓA vault -> email placeholder mất ->
            # booking gửi "{{email_1}}" -> 400 VALIDATION_ERROR -> REPROMPT khó hiểu).
            if s.phone:
                session.vault.pop(s.phone, None)                   # gỡ đúng số bị chặn khỏi vault
            s.phone = None
            s.contact_verified = False
            session.state = S.CONTACT                              # KHÔNG terminal -> vault (email) còn nguyên
            support = self._contact_phone(session, d.get("shop_phone"))
            return {"render_key": "ERROR",
                    "message": e.message + f" Anh/chị thử số điện thoại khác giúp em, "
                                           f"hoặc liên hệ hỗ trợ: {support}."}

        if code == "THERAPIST_OFF_SHIFT":                          # A4
            session.state = S.THERAPIST
            s.therapist_decided = False
            return {"render_key": "ERROR",
                    "message": e.message + " Anh/chị đổi giờ hay bỏ chỉ định nhân viên ạ?"}

        if code == "INVALID_COMBO":                                # A3 — combo course+add-on cấm
            session.state = S.ADDON
            s.addons_decided = False
            s.addon_guest_idx = 0
            bad = d.get("addon_id")
            if bad is not None:                                    # gỡ add-on gây cấm khỏi mọi người
                for guest in s.guest_addons:
                    if bad in guest:
                        guest.remove(bad)
            return {"render_key": "ERROR", "message": e.message}

        if code == "ADDON_WITHOUT_COURSE":                         # BR-01 — có add-on mà thiếu course
            session.state = S.COURSE
            s.course_id = None
            return {"render_key": "ERROR", "message": e.message}

        if code == "THERAPIST_NOT_ALLOWED":                        # BR-04
            s.therapist_id = None
            s.therapist_gender = None
            s.therapist_decided = True
            session.state = S.CONTACT
            return {"render_key": "ERROR", "message": e.message}

        if code == "PARTY_SIZE_EXCEEDED":                          # A8
            session.state = S.PARTY_SIZE
            s.party_size = None
            s.party_over = False
            return {"render_key": "HANDOFF", "message": e.message,
                    "shop_phone": self._contact_phone(session, d.get("shop_phone"))}

        if code == "BOOKING_NOT_FOUND":
            # Email không khớp khi xác thực sửa/hủy sau 2' -> gỡ email sai khỏi vault, XIN LẠI
            # (giữ nguyên các thay đổi đã chọn). Không kẹt ở email sai lần trước.
            if s.email:
                session.vault.pop(s.email, None)
            s.email = None
            session.awaiting_edit_email = True
            return {"render_key": "ERROR", "message": e.message}

        if code in ("MODIFY_DEADLINE_PASSED", "EDIT_TOKEN_EXPIRED", "SHOP_CHANGE_NOT_ALLOWED"):
            session.awaiting_edit_email = False        # lỗi KHÔNG do email -> đừng lặp xin email
            session.edit_email_for = ""
            return {"render_key": "ERROR", "message": e.message,
                    "shop_phone": self._contact_phone(session, d.get("shop_phone"))}

        if code == "VALIDATION_ERROR":
            return {"render_key": "REPROMPT"}

        # RATE_LIMITED / INTERNAL_ERROR / CHANNEL_UNAUTHORIZED — giữ state, mời thử lại (A7).
        return {"render_key": "ERROR",
                "message": e.message or "Hệ thống đang bận, anh/chị thử lại sau giây lát nhé."}

    # ------------------------------------------------------------------ #
    #  Handoff (MVP: chỉ đọc số cửa hàng cho khách gọi — Q9)              #
    # ------------------------------------------------------------------ #
    def _handoff(self, session: Session, reason_party: bool = False) -> BotReply:
        phone = self._contact_phone(session)
        message = ("Mỗi lượt đặt tối đa 3 người. " if reason_party else "")
        return self._reply(session, "HANDOFF",
                           {"message": message, "shop_phone": phone})

    # ------------------------------------------------------------------ #
    #  Màn chào                                                           #
    # ------------------------------------------------------------------ #
    def _greeting(self, session: Session) -> BotReply:
        # Câu chào cố định (không qua LLM để không tự đoán ngôn ngữ). Nói rõ đây là trợ lý AI
        # ngay từ đầu — yêu cầu minh bạch APPI (§6.3.4). Không còn nút chọn ngôn ngữ: chào
        # song ngữ rồi tự nhận ngôn ngữ theo câu khách trả lời (detect_lang), khách nói
        # "English"/"Tiếng Việt" giữa chừng vẫn đổi được (detect_lang_choice).
        # Kèm luôn danh sách cửa hàng để khách chọn được ngay từ câu đầu; tên cửa hàng là
        # DỮ LIỆU nên chỉ đọc một dòng chung, không dịch. API lỗi -> vẫn chào bình thường.
        try:
            names = ", ".join(sh["name"] for sh in self._get_shops())
        except ShopApiError:
            names = ""
        shops_line = f"\n🏬 Cửa hàng / Shops: {names}." if names else ""
        text = (
            "Xin chào 👋 Em là trợ lý đặt lịch AI, em giúp anh/chị đặt lịch massage ạ. "
            "Anh/chị muốn đặt ở cửa hàng nào ạ?\n"
            "Hi! I'm the AI booking assistant — which shop would you like to book?"
            f"{shops_line}"
        )
        session.history.append({"role": "bot", "masked_text": text})
        self.store.save(session)
        return BotReply(
            conversation_id=session.conversation_id,
            reply_text=text,
            state=session.state,
            done=False,
        )

    # ------------------------------------------------------------------ #
    #  ⑤⑥ dựng câu, unmask, lưu session                                  #
    # ------------------------------------------------------------------ #
    def _reply(self, session: Session, render_key: str, api_result: dict) -> BotReply:
        prompt = nlg.build_prompt(render_key, session, api_result, session.lang)
        reply = nlg.generate(prompt, self.llm)                     # ⑥ NLG (LLM hoặc fake)
        session.history.append({"role": "bot", "masked_text": reply})
        reply = pii.unmask(reply, session.vault)                   # trả PII thật cho widget khách

        session.maybe_drop_vault()                                 # Q5: rút vault sau 2'F
        self.store.save(session)

        return BotReply(
            conversation_id=session.conversation_id,
            reply_text=reply,
            state=session.state,
            done=S.is_terminal(session.state),
        )

    # ------------------------------------------------------------------ #
    #  Helpers                                                            #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _pii_resolved(value: str | None) -> bool:
        """PII đã giải ra giá trị THẬT chưa (không None, không còn là placeholder '{{...}}')."""
        return bool(value) and not value.startswith("{{")

    def _capture_contact_from_vault(self, session: Session) -> None:
        """SĐT/email khách gõ giữa câu -> masker đã nạp vào vault; gắn placeholder vào slots."""
        s = session.slots
        if not s.phone:
            s.phone = next((k for k in session.vault if k.startswith("{{phone_")), None)
        if not s.email:
            s.email = next((k for k in session.vault if k.startswith("{{email_")), None)

    def _match_course(self, session: Session, courses: list[dict]) -> bool:
        """Map course_text (gợi ý NLU) -> course_id nếu tên khớp; không khớp thì hỏi lại."""
        s = session.slots
        if s.course_id or not s.course_text:
            return False
        text = s.course_text.lower()
        s.course_text = None                  # tiêu thụ xong, tránh map lại ở lượt sau
        for c in courses:
            if text in c["name"].lower() or c["name"].lower() in text:
                s.course_id = c["id"]
                return True
        return False

    @staticmethod
    def _capture_choice_text(session: Session, asked: str, user_text: str) -> None:
        """Ở các bước CHỌN TỪ DANH SÁCH, cả câu khách nói chính là tên mục muốn chọn -> lấy
        làm gợi ý thô cho _match_* map về id.

        Cần vì NLU rule-based (chạy khi chưa cấu hình LLM, hoặc LLM lỗi) KHÔNG biết tên cửa
        hàng/course/add-on — chúng đến từ API chứ không nằm sẵn trong luật. Chỉ ghi khi
        NLU chưa cho gợi ý nào, để kết quả LLM (chính xác hơn) được ưu tiên."""
        s = session.slots
        text = (user_text or "").strip()
        if not text:
            return
        if asked == S.SHOP and s.shop_id is None and not s.shop_text:
            s.shop_text = text
        elif asked == S.COURSE and s.course_id is None and not s.course_text:
            s.course_text = text
        elif asked == S.ADDON and not s.addon_texts:
            s.addon_texts = [text]
        elif asked == S.THERAPIST and not s.therapist_decided and not s.therapist_text:
            s.therapist_text = text

    @staticmethod
    def _match_addons(session: Session, addons: list[dict]) -> bool:
        """Map tên add-on khách đọc (addon_texts) -> id, gán cho ĐÚNG người đang hỏi (BR-10)
        rồi sang người kế. Trả True nếu đã chốt được cho người này.

        Add-on cấm với course đang chọn (BR-09) bị bỏ qua — không mời thì cũng không nhận."""
        s = session.slots
        if not s.addon_texts:
            return False
        wanted = [t.strip().lower() for t in s.addon_texts if t and t.strip()]
        s.addon_texts = []                    # tiêu thụ xong, tránh gán lại cho người kế
        if not wanted:
            return False

        picked: list[int] = []
        for a in addons:
            if s.course_id and s.course_id in (a.get("restricted_course_ids") or []):
                continue
            name = (a.get("name") or "").lower()
            if not name:
                continue
            if any(t == name or t in name or name in t for t in wanted):
                picked.append(a["id"])
        if not picked:                        # đọc tên không khớp add-on nào -> hỏi lại
            return False

        s.ensure_guest_addons()
        idx = min(s.addon_guest_idx, len(s.guest_addons) - 1)
        s.guest_addons[idx] = picked
        sm.advance_addon_guest(s)
        return True

    @staticmethod
    def _match_shop(session: Session, shops: list[dict]) -> bool:
        """Map tên cửa hàng khách nêu (shop_text, vd 'Sendai') -> shop_id. Khớp -> chọn luôn,
        khỏi hỏi lại. Không khớp -> bỏ gợi ý để bot đọc lại danh sách cho khách chọn."""
        s = session.slots
        if s.shop_id is not None or not s.shop_text:
            return False
        text = s.shop_text.strip().lower()
        for sh in shops:
            name = sh["name"].lower()
            if text == name or text in name or name in text:
                s.shop_id = sh["id"]
                s.shop_text = None
                return True
        s.shop_text = None
        return False

    @staticmethod
    def _match_therapist(session: Session, therapists: list[dict]) -> bool:
        """Map tên nhân viên khách nêu (therapist_text, vd 'Hana') -> therapist_id.
        Khớp -> chỉ định luôn, khỏi hỏi. Không khớp -> bỏ hint để khách chọn bằng nút."""
        s = session.slots
        if s.therapist_decided or not s.therapist_text:
            return False
        text = s.therapist_text.strip().lower()
        for t in therapists:
            name = t["name"].lower()
            if text == name or text in name or name in text:
                s.therapist_id = t["id"]
                s.therapist_gender = None
                s.therapist_decided = True
                s.therapist_text = None       # đã dùng xong -> tránh map lại (vd khi kín lịch)
                s.slot = None; s.confirm = None
                return True
        s.therapist_text = None               # tên không khớp ai -> bot đọc lại danh sách
        return False

    @staticmethod
    def _future_slots(slots: list[str], date_str: str | None) -> list[str]:
        """Bỏ các giờ ĐÃ QUA nếu đặt cho HÔM NAY — không cho đặt lùi về quá khứ."""
        from datetime import date as _date, datetime as _dt
        if not date_str or date_str != _date.today().isoformat():
            return slots
        now_min = _dt.now().hour * 60 + _dt.now().minute

        def _mins(t: str) -> int:
            h, m = t.split(":")
            return int(h) * 60 + int(m)

        out = []
        for t in slots:
            try:
                if _mins(t) > now_min:        # chỉ giữ giờ còn ở tương lai
                    out.append(t)
            except (ValueError, IndexError):
                out.append(t)
        return out

    @staticmethod
    def _union_addons(session: Session) -> list[int]:
        """Hợp add-on của mọi người — gửi cho GET /slots (chỉ nhận 1 bộ). Giữ thứ tự, bỏ trùng."""
        seen: list[int] = []
        for guest in session.slots.guest_addons:
            for a in guest:
                if a not in seen:
                    seen.append(a)
        return seen

    @staticmethod
    def _cache_course(session: Session, courses: list[dict]) -> None:
        """Lưu tên + thời lượng course đã chọn để đọc lại ở CONFIRM (không cần gọi API lần nữa)."""
        cid = session.slots.course_id
        if not cid:
            return
        for c in courses:
            if c["id"] == cid:
                session.slots.course_name = c["name"]
                session.slots.duration = c.get("duration_min")
                return

    @staticmethod
    def _order_slots(slots: list[str], wanted: str | None, limit: int = 16) -> list[str]:
        """Sắp giờ trống theo thứ tự thời gian.

        - KHÔNG có 'giờ mong muốn' -> hiện HẾT giờ trống trong ngày (khớp FE — tránh cắt
          đuôi làm mất giờ chiều/tối như 18:00).
        - CÓ 'giờ mong muốn' và quá nhiều slot -> lấy các giờ GẦN nhất (tối đa `limit`),
          rồi sắp lại theo thời gian để dễ nhìn.
        """
        def _mins(t: str) -> int:
            h, m = t.split(":")
            return int(h) * 60 + int(m)

        try:
            chrono = sorted(slots, key=_mins)
        except (ValueError, IndexError):
            return slots

        if wanted and len(chrono) > limit:
            try:
                w = _mins(wanted)
                nearest = sorted(chrono, key=lambda t: abs(_mins(t) - w))[:limit]
                return sorted(nearest, key=_mins)
            except (ValueError, IndexError):
                pass
        return chrono

    def _get_shops(self) -> list[dict]:
        now = time.time()
        if self._shops_cache and now - self._shops_cache[0] < 300:
            return self._shops_cache[1]
        shops = self.api.get_shops()
        self._shops_cache = (now, shops)
        return shops

    def _available_dates(self, shop_id: int | None) -> list[str] | None:
        """Các ngày cửa hàng THỰC SỰ có ca làm trong ~2 tuần tới (mở = có ca, cùng tín hiệu
        has_shifts của GET /services). Lấy trong MỘT lần gọi GET /shops/{id}/availability.

        Trả list ISO (có thể RỖNG = shop nghỉ suốt horizon) khi gọi được; None khi lỗi API
        để câu hỏi ngày lùi về hỏi chung chung thay vì tưởng shop đóng cửa. Cache 5'/shop."""
        if not shop_id:
            return None
        from datetime import date as _date, timedelta as _td

        now = time.time()
        cached = self._avail_cache.get(shop_id)
        if cached and now - cached[0] < 300:
            return cached[1]

        today = _date.today()
        to = today + _td(days=self._AVAIL_HORIZON_DAYS - 1)
        try:
            data = self.api.get_availability(shop_id, today.isoformat(), to.isoformat())
        except ShopApiError:
            return None                         # không rõ -> không đọc danh sách ngày

        open_days = data.get("open_dates") or []
        self._avail_cache[shop_id] = (now, open_days)
        return open_days

    def _contact_phone(self, session: Session, be_phone: str | None = None) -> str:
        """Số để khách LIÊN HỆ khi không đặt online được (chặn NG A5 / handoff / nhóm đông A8).
        Ưu tiên số hỗ trợ/CSKH ở env (SUPPORT_PHONE, hoặc FALLBACK_SHOP_PHONE) để mọi ca 'liên
        hệ' về một đầu mối; chưa cấu hình mới dùng số cửa hàng (BE trả, rồi /shops)."""
        return (self.settings.support_phone or self.settings.fallback_shop_phone
                or be_phone or self._shop_phone(session))

    def _shop_phone(self, session: Session) -> str:
        if session.shop_phone:
            return session.shop_phone
        try:
            if session.slots.shop_id:
                for sh in self._get_shops():
                    if sh["id"] == session.slots.shop_id:
                        session.shop_phone = sh["phone"]
                        return sh["phone"]
        except ShopApiError:
            pass
        return self.settings.fallback_shop_phone or ""
