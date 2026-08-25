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
from dataclasses import asdict, dataclass, field

from app import answers, matching, nlg, nlu, pii, templates, turnlog
from app import state_machine as sm
from app import states as S
from app.config import Settings
from app.llm_client import RealLLMClient
from app.session import Session, SessionStore
from app.shop_api_client import ShopApiClient, ShopApiError


# Khớp tên -> app/matching.py (answers/ dùng chung; import ngược vào đây sẽ vòng).
# Giữ alias tên cũ để _match_shop/_match_course/... và test hiện có không phải sửa.
_name_matches = matching.name_matches
_pick_unique = matching.pick_unique


class _AnswerApi:
    """Mặt tiền CHỈ-ĐỌC cho tủ tra cứu: dùng lại cache sẵn có của Orchestrator (shops/
    services) và thêm cache timeline. Resolver chỉ thấy các hàm ĐỌC, không thấy Session."""

    def __init__(self, orch: "Orchestrator"):
        self._o = orch

    def get_shops(self):
        return self._o._get_shops()

    def get_services(self, shop_id, date, party_size=None):
        return self._o._get_services(shop_id, date, party_size)

    def get_availability(self, shop_id, date_from, date_to):
        return self._o.api.get_availability(shop_id, date_from, date_to)

    def get_timeline(self, shop_id, date):
        return self._o._get_timeline(shop_id, date)


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
        # (shop_id, date, party_size) -> (epoch, data). Bước COURSE khớp được tên rồi tiến
        # thẳng sang ADDON, mà cả hai bước đều cần /services -> nếu không cache thì MỘT lượt
        # chat gọi API hai lần liên tiếp với y hệt tham số.
        self._services_cache: dict[tuple, tuple[float, dict]] = {}
        # (shop_id, date) -> (epoch, data) cho GET /timeline. Câu hỏi "mở lúc mấy giờ" phải
        # dò LẦN LƯỢT từng cửa hàng nên không cache là mỗi lượt nện API n lần.
        self._timeline_cache: dict[tuple, tuple[float, dict]] = {}
        self._answer_api = _AnswerApi(self)

    # ------------------------------------------------------------------ #
    #  Vòng 1 lượt                                                        #
    # ------------------------------------------------------------------ #
    def handle_turn(self, conversation_id: str | None, user_text: str) -> BotReply:
        cid = conversation_id or str(uuid.uuid4())
        session = self.store.load(cid) or Session(conversation_id=cid)
        session.turn_count += 1
        # Cả lượt gom vào MỘT bản ghi log, phát ra ở finally -> không xen kẽ giữa các hội
        # thoại chạy song song, và luôn có khối kể cả khi lượt ném ngoại lệ.
        turnlog.start(cid, session.turn_count, session.state)
        try:
            reply = self._handle_turn(session, user_text)
            turnlog.out(reply.reply_text)
            return reply
        finally:
            turnlog.finish(session.state, self._slots_brief(session))

    @staticmethod
    def _slots_brief(session: Session) -> str:
        """Tóm tắt tờ đơn cho dòng cuối khối log — chỉ những ô ĐÃ điền."""
        s = session.slots
        got = [(k, v) for k, v in (("shop", s.shop_id), ("ngày", s.date),
                                   ("người", s.party_size), ("gói", s.course_id),
                                   ("giờ", s.slot)) if v is not None]
        brief = " ".join(f"{k}={v}" for k, v in got) or "(đơn trống)"
        return brief + (f" mã={session.booking_code}" if session.booking_code else "")

    def _handle_turn(self, session: Session, user_text: str) -> BotReply:
        # Mở chat (text rỗng) -> câu chào.
        if not (user_text or "").strip():
            turnlog.inp("(mở chat)")
            turnlog.lane("META", "câu chào")
            return self._greeting(session)

        # Đang chờ khách NHẬP LẠI EMAIL để xác thực sửa/hủy sau cửa sổ 2' (BR-15) — dòng này
        # là email khách gõ, không phải câu đặt lịch: nạp email rồi làm nốt thao tác đang chờ.
        if session.awaiting_edit_email:
            turnlog.inp("(email xác thực sửa/hủy)")
            turnlog.lane("META", "nhập lại email")
            return self._resume_with_edit_email(session, user_text)

        # State bot ĐANG hỏi — suy từ slots nên đúng cả khi state đã lưu chưa kịp cập nhật
        # (vd ngay sau câu chào). Cần để hiểu những câu chỉ có nghĩa trong ngữ cảnh câu hỏi:
        # "31" = ngày, "Shop A" = tên cửa hàng, "không" = không thêm add-on.
        asked = sm.next_state(session)

        # Đang ở menu "đổi gì" (UC-02): câu này là chọn phần muốn đổi, không phải câu đặt mới.
        if session.editing and nlu.is_cancel_request(user_text):
            turnlog.inp(user_text)
            turnlog.lane("META", "hủy lịch")
            return self._cancel(session)
        elif session.editing and (target := nlu.detect_modify_target(user_text)) is not None:
            turnlog.inp(user_text)
            turnlog.lane("META", f"đổi {target}")
            sm.apply_modify_target(session, target)
        else:
            # ① NLU (LLM) — mask PII trước khi ra LLM (bước ⑥.1 của masker).
            extra = [session.booking_code] if session.booking_code else None
            masked = pii.mask(user_text, session.vault, extra_values=extra)
            turnlog.inp(masked)
            session.history.append({"role": "user", "masked_text": masked})

            parsed = nlu.extract(masked, self.llm)
            if parsed is None:                       # sai schema -> hỏi lại (§3.4)
                return self._reply(session, "REPROMPT", {})

            if parsed["intent"] == "handoff":
                turnlog.lane("META", "xin gặp người thật")
                return self._handoff(session)

            # GÁC CỬA — lượt này là CÂU HỎI thông tin hay giá trị điền vào tờ đơn? Phải xét
            # TRƯỚC khối booking_code bên dưới: đặt xong rồi mà khách hỏi "shop ở đâu" thì
            # intent=book sẽ bị hiểu thành muốn sửa lịch.
            if self._is_question(parsed, user_text):
                turnlog.lane("QUERY", parsed["question_type"])
                return self._answer_query(session, parsed, masked, asked)

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

            turnlog.lane("TASK")
            before = asdict(session.slots)
            self._capture_contact_from_vault(session)  # phone/email từ vault (Q6 lưới hứng)
            # ② MERGE
            sm.merge_params(session, parsed["entities"])

            # Đang hỏi NGÀY mà khách trả lời số trần ("31") hay "31/7" — NLU stateless bỏ số
            # trần để khỏi nhầm với số người; ở đây có ngữ cảnh nên diễn giải thành ngày.
            if asked == S.DATE and session.slots.date is None:
                iso = nlu.parse_date_freeform(user_text, allow_bare_day=True)
                if iso:
                    sm.merge_params(session, {"date": iso})

            # Đang hỏi SỐ NGƯỜI mà khách trả lời số trần ("3") — NLU stateless cũng bỏ
            # (log thật: LLM trả chitchat/null cho '3' khiến bot hỏi lại); ở đây có ngữ
            # cảnh nên diễn giải thành số người. merge_params tự xử lý >3 (party_over).
            bare = (user_text or "").strip()
            if asked == S.PARTY_SIZE and session.slots.party_size is None \
                    and bare.isdigit() and len(bare) <= 2:
                sm.merge_params(session, {"party_size": int(bare)})

            # Đang hỏi ADD-ON mà khách nói "không"/"thôi" -> chốt không thêm gì. Chỉ hiểu
            # được theo ngữ cảnh: cùng chữ "không" ở bước CONFIRM lại là từ chối cả đơn.
            if asked == S.ADDON and nlu.is_negative(user_text):
                sm.skip_addons(session)
                # "Không" ở đây nghĩa là KHÔNG THÊM ADD-ON, không phải từ chối cả đơn —
                # nhưng NLU (stateless) trả confirm='no'. Gỡ ra, nếu không đơn mang sẵn
                # trạng thái "đã từ chối" tới tận bước CONFIRM.
                if session.slots.confirm == "no":
                    session.slots.confirm = None
            else:
                self._capture_choice_text(session, asked, user_text)
            turnlog.merge(self._slots_diff(before, asdict(session.slots)))

        # Nhóm >3 -> handoff (BR-14 / A8), không cần gọi BE.
        if session.slots.party_over:
            return self._handoff(session, reason_party=True)

        # ③ STATE MACHINE
        _prev_state = session.state
        session.state = sm.next_state(session)
        turnlog.state(_prev_state, session.state)
        # ④ VALIDATE + CALL API (có thể đổi session.state theo A1/A2/lỗi)
        _state3 = session.state
        api_result = self._run_state_action(session)
        if session.state != _state3:
            # Bước ④ tự đẩy tiếp (khớp được tên shop/gói/nhân viên) hoặc lùi lại (A1/A2/lỗi).
            # Không ghi thì log nhìn mâu thuẫn: ③ báo SHOP mà lượt kết thúc ở DATE.
            turnlog.state(_state3, session.state)

        # ⑤⑥ NLG
        render_key = api_result.get("render_key", session.state)
        return self._reply(session, render_key, api_result)

    # ------------------------------------------------------------------ #
    #  Làn QUERY — khách HỎI thông tin, không phải điền đơn                #
    # ------------------------------------------------------------------ #
    _OFFTOPIC_LIMIT = 3               # lạc đề liên tiếp bấy nhiêu lượt -> mời gọi cửa hàng

    def _is_question(self, parsed: dict, user_text: str) -> bool:
        """Ưu tiên luồng đặt lịch: đoán nhầm thành "hỏi" chỉ làm bot trả lời thừa, đoán
        nhầm ngược lại làm hỏng cả phiên đặt. Nghi ngờ -> coi là điền đơn.

        KHÔNG tin một mình question_type của NLU: log thật cho thấy nó gán "other" cho
        "Sendai" và "course_price" cho "Gói đầu tiên" — toàn là câu khách TRẢ LỜI. Phải có
        thêm dấu hiệu hỏi trong chính câu nói."""
        if parsed.get("question_type") not in answers.HANDLED:
            return False
        text = (user_text or "").strip()
        if not nlu.looks_like_question(text):
            return False
        # LLM bảo là câu đặt lịch mà vẫn gán loại câu hỏi -> chỉ tin khi có dấu "?" hẳn hoi.
        if parsed.get("intent") != "ask_info" and "?" not in text:
            return False
        return True

    def _answer_query(self, session: Session, parsed: dict, masked: str,
                      asked: str) -> BotReply:
        """Trả lời rồi đọc lại câu đang dở. KHÔNG đụng session.state -> lượt sau hội thoại
        chạy tiếp đúng chỗ, tờ đơn nguyên vẹn."""
        ent = parsed["entities"]
        ctx = answers.QueryCtx(
            question_type=parsed["question_type"],
            entities=ent,
            shop_id=session.slots.shop_id,
            date=session.slots.date,
            party_size=session.slots.party_size,
            time_ambiguous=self._time_ambiguous(ent.get("time"), masked),
            raw_text=masked,
        )
        ans = answers.resolve(ctx, self._answer_api)

        if not ans.resolved:
            session.offtopic_count += 1
            if session.offtopic_count >= self._OFFTOPIC_LIMIT:
                return self._handoff(session)
            return self._reply(session, "OUT_OF_SCOPE",
                               {"cau_hoi": self._pending_question(session, asked)})

        session.offtopic_count = 0
        if ans.suggest:
            sm.merge_params(session, ans.suggest)   # CỬA GHI DUY NHẤT vào tờ đơn
            # merge_params chỉ ghi shop_text; map sang id ngay bằng đúng đường _match_shop
            # (danh sách shop vừa được resolver lấy nên đang nằm trong cache) để câu đọc lại
            # nhảy sang bước kế, không hỏi lại đúng thứ vừa chốt.
            if session.slots.shop_text:
                try:
                    self._match_shop(session, self._get_shops())
                except ShopApiError:
                    pass
            asked = sm.next_state(session)          # điền được ô -> câu đang dở đã khác
        return self._reply(session, "INFO",
                           {"noi_dung": ans.text,
                            "cau_hoi": self._pending_question(session, asked)})

    @staticmethod
    def _time_ambiguous(time_str: str | None, text: str) -> bool:
        """"7h" trần là mơ hồ (7h sáng hay 7h tối) -> tủ tra cứu trả lời cả hai."""
        if not time_str:
            return False
        try:
            if int(str(time_str)[:2]) >= 12:
                return False
        except ValueError:
            return False
        return not nlu.has_daypart(text)

    @staticmethod
    def _pending_question(session: Session, asked: str) -> str:
        if S.is_terminal(session.state):
            return templates.PENDING_QUESTION_DEFAULT
        return templates.PENDING_QUESTION.get(asked, templates.PENDING_QUESTION_DEFAULT)

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
                data = self._get_services(s.shop_id, s.date, s.party_size)
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

            if st == S.ADDON:                                     # bước RIÊNG sau course
                data = self._get_services(s.shop_id, s.date, s.party_size)
                self._cache_course(session, data.get("courses", []))
                addons = data.get("addons", [])
                if self._match_addons(session, addons):
                    # Khách đã đọc tên add-on -> chốt luôn, khỏi hỏi lại.
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                return {"addons": addons}

            if st == S.SLOT:
                data = self.api.get_slots(
                    s.shop_id, date=s.date, party_size=s.party_size, course_id=s.course_id,
                    # Cả nhóm dùng chung một bộ add-on nên gửi thẳng (BE vẫn re-check lúc tạo).
                    addon_ids=list(s.addon_ids), therapist_id=s.therapist_id,
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
                # nút giờ trước đây).
                if s.wanted_time and s.wanted_time in slots:
                    s.slot = s.wanted_time
                    s.wanted_time = None
                    session.state = sm.next_state(session)
                    return self._run_state_action(session)
                # Giờ khách nêu KHÔNG còn trống -> phải NÓI RÕ là giờ đó hết, rồi mới đọc
                # danh sách. Trước đây chỉ đọc danh sách nên khách tưởng bot bỏ qua lời mình
                # (vd nói "7h tối nay" mà shop chỉ mở tới 16:30).
                wanted = s.wanted_time
                s.wanted_time = None          # đã báo rồi -> đừng lặp lại ở lượt sau
                if wanted:
                    return {"slots": self._order_slots(slots, wanted),
                            "wanted_time_unavailable": wanted}
                return {"slots": self._order_slots(slots, None)}

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

        # BR-10 (BA cập nhật): cả nhóm CÙNG course và CÙNG add-on -> lặp lại một bộ cho
        # từng reservation (API vẫn nhận add-on theo từng người, ta gửi giống nhau).
        reservations = [{"addon_ids": list(s.addon_ids)}
                        for _ in range(s.party_size or 1)]
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

        # BR-10 (BA cập nhật): cả nhóm CÙNG course và CÙNG add-on -> lặp lại một bộ cho
        # từng reservation (API vẫn nhận add-on theo từng người, ta gửi giống nhau).
        reservations = [{"addon_ids": list(s.addon_ids)}
                        for _ in range(s.party_size or 1)]
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
            bad = d.get("addon_id")
            if bad is not None and bad in s.addon_ids:             # gỡ add-on gây cấm
                s.addon_ids.remove(bad)
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
        # Câu chào cố định (không qua LLM). Nói rõ đây là trợ lý AI ngay từ đầu — yêu cầu
        # minh bạch APPI (§6.3.4). Kèm luôn danh sách cửa hàng để khách chọn được ngay từ
        # câu đầu; API lỗi -> vẫn chào bình thường.
        try:
            names = ", ".join(sh["name"] for sh in self._get_shops())
        except ShopApiError:
            names = ""
        shops_line = f"\n🏬 Cửa hàng: {names}." if names else ""
        text = (
            "Xin chào 👋 Em là trợ lý đặt lịch AI, em giúp anh/chị đặt lịch massage ạ. "
            "Anh/chị muốn đặt ở cửa hàng nào ạ?"
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
        prompt = nlg.build_prompt(render_key, session, api_result)
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

    @staticmethod
    def _match_course(session: Session, courses: list[dict]) -> bool:
        """Map course_text (gợi ý NLU) -> course_id nếu tên khớp DUY NHẤT; không khớp hay
        mơ hồ (vd "Momihogushi" trúng cả 4 mức thời lượng) thì hỏi lại."""
        s = session.slots
        if s.course_id or not s.course_text:
            return False
        text, s.course_text = s.course_text, None      # tiêu thụ xong, tránh map lại lượt sau
        c = _pick_unique(text, courses)
        if c is None:
            # NLU hay XÉ số phút khỏi tên gói ("momihogushi 30" -> course='momihogushi' +
            # duration=30), làm tên còn lại trúng cả 4 mức thời lượng -> mơ hồ -> hỏi lại
            # đúng thứ khách vừa nói. Thử lại bằng CÂU GỐC, ở đó số phút vẫn còn.
            c = _pick_unique(Orchestrator._last_user_text(session), courses)
        if c is None:
            return False
        s.course_id = c["id"]
        return True

    @staticmethod
    def _slots_diff(before: dict, after: dict) -> list[str]:
        """Những ô của tờ đơn đã đổi trong lượt — để đọc log biết bước ② thực sự ghi gì."""
        out = []
        for k, new in after.items():
            old = before.get(k)
            if old != new:
                out.append(f"{k}: {old!r}→{new!r}")
        return out

    @staticmethod
    def _last_user_text(session: Session) -> str:
        """Câu khách vừa nói (đã mask) — handle_turn nạp vào history ngay trước bước NLU.
        Dùng làm phương án 2 khi gợi ý NLU không khớp được mục nào."""
        last = session.history[-1] if session.history else None
        if last and last.get("role") == "user":
            return last.get("masked_text") or ""
        return ""

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
        """Map tên add-on khách đọc -> id. Nhận NHIỀU add-on trong MỘT câu ("Ashitsubo với
        Hot Stone") — trước đây dùng _pick_unique nên câu nêu 2 tên bị coi là mơ hồ rồi bỏ
        sạch, khiến chat chỉ chọn được 1 trong khi web tick được nhiều.

        Cả nhóm dùng CHUNG một bộ add-on (BR-10, BA cập nhật). Add-on cấm với course đang
        chọn (BR-09) bị loại — không mời thì cũng không nhận."""
        s = session.slots
        if not s.addon_texts:
            return False
        # NLU trả danh sách tên, còn nhánh không-LLM đưa nguyên câu vào một phần tử -> nối
        # hết lại rồi quét một lượt, xử lý được cả hai dạng.
        query = " ".join(t.strip() for t in s.addon_texts if t and t.strip()).lower()
        s.addon_texts = []                    # tiêu thụ xong, tránh gán lại ở lượt sau
        if not query:
            return False

        allowed = [a for a in addons
                   if not (s.course_id and s.course_id in (a.get("restricted_course_ids") or []))]
        picked = [a["id"] for a in matching.pick_all(query, allowed)]
        if not picked:                        # đọc tên không khớp add-on nào -> hỏi lại
            return False

        s.addon_ids = picked
        s.addons_decided = True
        return True

    @staticmethod
    def _match_shop(session: Session, shops: list[dict]) -> bool:
        """Map tên cửa hàng khách nêu (shop_text, vd 'Sendai') -> shop_id. Khớp DUY NHẤT ->
        chọn luôn, khỏi hỏi lại. Không khớp / mơ hồ -> bot đọc lại danh sách cho khách chọn."""
        s = session.slots
        if s.shop_id is not None or not s.shop_text:
            return False
        text, s.shop_text = s.shop_text, None
        sh = _pick_unique(text, shops)
        if sh is None:
            return False
        s.shop_id = sh["id"]
        s.shop_name = sh.get("name")      # để đọc lại ở CONFIRM (khách cần thấy đặt shop nào)
        return True

    @staticmethod
    def _match_therapist(session: Session, therapists: list[dict]) -> bool:
        """Map tên nhân viên khách nêu (therapist_text, vd 'Hana') -> therapist_id.
        Khớp DUY NHẤT -> chỉ định luôn, khỏi hỏi. Không khớp / mơ hồ -> đọc lại danh sách."""
        s = session.slots
        if s.therapist_decided or not s.therapist_text:
            return False
        text, s.therapist_text = s.therapist_text, None   # tiêu thụ xong, tránh map lại
        t = _pick_unique(text, therapists)
        if t is None:
            return False
        s.therapist_id = t["id"]
        s.therapist_gender = None
        s.therapist_decided = True
        s.slot = None; s.confirm = None       # giờ trống phụ thuộc người phục vụ -> chọn lại giờ
        return True

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

    _SERVICES_TTL = 60          # course/add-on ít đổi; đủ ngắn để admin sửa xong là thấy ngay

    def _get_services(self, shop_id: int | None, date: str | None,
                      party_size: int | None) -> dict:
        """GET /services có cache ngắn theo (shop, ngày, số người) — xem _services_cache."""
        key = (shop_id, date, party_size)
        now = time.time()
        hit = self._services_cache.get(key)
        if hit and now - hit[0] < self._SERVICES_TTL:
            return hit[1]
        data = self.api.get_services(shop_id, date, party_size)
        self._services_cache[key] = (now, data)
        return data

    _TIMELINE_TTL = 60

    def _get_timeline(self, shop_id: int, date: str) -> dict:
        """GET /timeline có cache ngắn theo (shop, ngày) — xem _timeline_cache."""
        key = (shop_id, date)
        now = time.time()
        hit = self._timeline_cache.get(key)
        if hit and now - hit[0] < self._TIMELINE_TTL:
            return hit[1]
        data = self.api.get_timeline(shop_id, date)
        self._timeline_cache[key] = (now, data)
        return data

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
