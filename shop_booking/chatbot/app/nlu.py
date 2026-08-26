"""NLU (bước ①) — DD §2.3/§3.4, chatbot-architecture.md §3.4, Q4.

LLM chỉ TRÍCH param -> JSON cố định, KHÔNG trả lời. Code validate JSON trước khi merge
(sai schema -> coi như không trích được -> hỏi lại). Đây là ranh giới "LLM hiểu" ↔ "code
quyết" và là chỗ chống prompt injection tầng client.

Không cấu hình router (llm=None) -> nhánh rule-based offline: đủ cho dev/test luồng chính.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, timedelta

from app import turnlog
from app.llm_client import LLMError, RealLLMClient

logger = logging.getLogger(__name__)

INTENTS = {"book", "modify", "cancel", "ask_info", "chitchat", "handoff"}
_ENTITY_KEYS = ("shop", "date", "time", "party_size", "duration", "course", "addons",
                "therapist", "confirm", "location")

# Loại câu hỏi thông tin (không phải điền đơn) — khớp key trong app/answers/RESOLVERS.
QUESTION_TYPES = {"shops_open_at", "shop_contact", "shop_days_off", "course_price",
                  "shops_near", "shops_list", "shops_by_staff", "faq", "other"}

_NLU_SYSTEM = (
    "Bạn là bộ trích xuất tham số cho hệ thống đặt lịch massage. CHỈ trích xuất, TUYỆT ĐỐI "
    "KHÔNG trả lời khách. Trả về DUY NHẤT một JSON đúng schema:\n"
    '{"intent":"book|modify|cancel|ask_info|chitchat|handoff",'
    '"entities":{"shop":"text|null","date":"YYYY-MM-DD|null","time":"HH:MM|null",'
    '"party_size":"1|null","duration":"60|null","course":"text|null","addons":[],'
    '"therapist":"name|male|female|none|null","confirm":"yes|no|null",'
    '"location":"text|null"},'
    '"question_type":"shops_open_at|shop_contact|shop_days_off|course_price|shops_near|shops_list|shops_by_staff|faq|other|null"}\n'
    "question_type CHỈ điền khi khách ĐANG HỎI thông tin về cửa hàng (giờ mở cửa, địa chỉ, "
    "số điện thoại, ngày nghỉ, giá dịch vụ, cửa hàng gần khu vực nào); khách đang TRẢ LỜI "
    "câu hỏi của trợ lý thì để null. location là khu vực/địa chỉ CỦA KHÁCH (nhà/chỗ khách "
    "đang đứng), KHÔNG phải tên cửa hàng.\n"
    "Dùng question_type='faq' cho câu hỏi về CHÍNH SÁCH/QUY TRÌNH — đổi lịch, hủy lịch, "
    "đặt tối đa mấy người, có chỉ định được nhân viên không, add-on đặt riêng được không, "
    "mã đặt chỗ, quy định đến muộn. Mấy thứ này không tra bảng nào mà tra tài liệu.\n"
    "course là TÊN GÓI ĐÚNG NHƯ KHÁCH NÓI, GIỮ NGUYÊN cả số phút trong tên "
    "(vd 'massage body 30' -> course='massage body 30', TUYỆT ĐỐI không tách 30 sang duration) "
    "— tên thiếu số phút sẽ trùng nhiều gói và hệ thống không chọn được.\nn"
    "QUAN TRỌNG: date PHẢI là ngày tuyệt đối YYYY-MM-DD. Khách nói tương đối (hôm nay/mai/"
    "ngày kia/thứ Hai tuần sau) thì tự quy đổi dựa trên 'Hôm nay' được cung cấp; time là 24h "
    "HH:MM. shop LÀ TÊN/ĐỊA ĐIỂM cửa hàng khách nêu (vd 'Hải Châu', 'Sài Gòn', 'chi nhánh Huế') — "
    "chỉ trích khi khách CHỈ RÕ cửa hàng, không suy diễn. Không thêm chữ nào ngoài JSON. "
    "Không suy diễn giá trị khách không nói (để null)."
)


# --------------------------------------------------------------------------- #
#  Public                                                                      #
# --------------------------------------------------------------------------- #

def extract(masked_text: str, llm: RealLLMClient | None,
            timeout: float | None = None) -> dict | None:
    """Trả {'intent', 'entities'} đã validate, hoặc None nếu không trích được (hỏi lại)."""
    _t0 = time.perf_counter()
    if llm is None:
        parsed = _rule_based(masked_text)
        source = "rule_based (chưa cấu hình LLM)"
    else:
        parsed = None
        try:
            today = date.today()
            raw = llm.complete(
                _NLU_SYSTEM,
                f"[Hôm nay={today.isoformat()} ({today:%A})] {masked_text}",
                temperature=0.0, max_tokens=400, response_json=True, timeout=timeout,
            )
            parsed = validate_schema(_parse_json(raw))
        except LLMError:
            parsed = None
        source = "llm"
        # Router LỖI *hoặc* trả JSON sai/không parse được (router hay "nói" thay vì trích) ->
        # thử rule-based rồi mới bó tay. Trước đây chỉ fallback khi LLMError, nên câu rõ như
        # "đồng ý đặt" mà router trả text thường -> None -> REPROMPT oan (bot "suy nghĩ sai").
        if parsed is None:
            parsed = _rule_based(masked_text)
            source = "rule_based (LLM lỗi hoặc JSON sai schema)"

    if parsed is None:
        logger.warning("nlu: không trích được gì từ %r -> hỏi lại", masked_text)
        return None
    if not parsed.get("question_type"):
        # Router hay bỏ field mới. Nó vẫn gán intent=ask_info đúng, nên suy LOẠI câu hỏi
        # bằng luật — thiếu cái này thì câu hỏi rơi tuột về luồng đặt lịch (bug đã gặp).
        parsed["question_type"] = _detect_question(masked_text.lower())
    parsed["entities"] = _normalize_entities(parsed["entities"])  # date tương đối -> ISO
    turnlog.nlu(source, time.perf_counter() - _t0, parsed["intent"],
                parsed["question_type"], parsed["entities"])
    logger.debug("nlu: text=%r source=%s -> %s", masked_text, source, parsed)
    return parsed


def validate_schema(obj) -> dict | None:
    """Chuẩn hóa + kiểm tra schema NLU. Sai -> None."""
    if not isinstance(obj, dict):
        return None
    intent = obj.get("intent")
    if intent not in INTENTS:
        intent = "book"  # thiếu/lạ intent -> mặc định đặt lịch, vẫn chạy tiếp
    ent_in = obj.get("entities")
    if not isinstance(ent_in, dict):
        return None
    entities = {}
    for k in _ENTITY_KEYS:
        v = ent_in.get(k)
        if v in ("null", "", "none") and k != "therapist":
            v = None
        entities[k] = v if k != "addons" else (v or [])
    qt = obj.get("question_type")
    if qt not in QUESTION_TYPES:            # thiếu/lạ -> coi như không phải câu hỏi
        qt = None
    return {"intent": intent, "entities": entities, "question_type": qt}


def _normalize_entities(entities: dict) -> dict:
    """Lưới an toàn: nếu LLM vẫn trả date tương đối ('tomorrow'/'mai'…) hay dạng rời
    ('31/7', 'ngày 31 tháng 8') thay vì ISO, quy về YYYY-MM-DD ở đây; không quy được thì
    bỏ (None) để state machine hỏi lại — thà hỏi lại còn hơn để 'tomorrow' lọt vào
    slots.date rồi shop_api báo lỗi.

    KHÔNG suy số trần ('31') ở đây: NLU không biết đang ở bước nào nên '3' có thể là số
    người; số trần chỉ được diễn giải theo NGỮ CẢNH ở orchestrator khi đang hỏi ngày."""
    d = entities.get("date")
    if d:
        entities["date"] = _to_iso_date(d) or parse_date_freeform(str(d), allow_bare_day=False)
    return entities


_REL_TODAY = {"hôm nay", "hom nay", "nay"}
_REL_TOMORROW = {"ngày mai", "ngay mai", "mai"}
_REL_DAY_AFTER = {"ngày kia", "ngay kia", "mốt", "mot"}


def _match_rel(low: str, words: set[str]) -> bool:
    """Khớp từ chỉ ngày tương đối. Cần RANH GIỚI TỪ (\\b) để 'mai' không dính trong 'email',
    'nay' không dính trong 'ngày'…"""
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)


def _to_iso_date(value: str) -> str | None:
    v = str(value).strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    if v in _REL_TODAY:
        return date.today().isoformat()
    if v in _REL_TOMORROW:
        return (date.today() + timedelta(days=1)).isoformat()
    if v in _REL_DAY_AFTER:
        return (date.today() + timedelta(days=2)).isoformat()
    return None


def parse_date_freeform(text: str, *, allow_bare_day: bool = False,
                        today: date | None = None) -> str | None:
    """Diễn giải NGÀY khách gõ tự do -> 'YYYY-MM-DD', lấy lần xuất hiện GẦN NHẤT >= hôm nay.

    Hiểu: ISO đầy đủ; tương đối (hôm nay/mai/mốt…); 'd/m' · 'd-m' · 'd.m' (+ năm tùy chọn);
    'ngày D [tháng M]' · 'D tháng M'. Khi `allow_bare_day` thì hiểu cả SỐ TRẦN 'D' — chỉ
    bật khi ĐANG hỏi ngày, để '3' ở bước khác không bị hiểu nhầm là ngày mùng 3.

    Thiếu tháng -> chọn tháng gần nhất mà ngày đó còn ở tương lai (vd hôm nay 27/7, gõ '5'
    -> 5/8). Không hợp lệ (vd '31/2', '99') -> None để bot hỏi lại."""
    if not text:
        return None
    today = today or date.today()
    low = text.strip().lower()

    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", low)          # ISO
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3])).isoformat()
        except ValueError:
            return None

    for words, delta in ((_REL_TODAY, 0), (_REL_TOMORROW, 1), (_REL_DAY_AFTER, 2)):
        if _match_rel(low, words):                     # ranh giới từ: 'mai' KHÔNG khớp trong 'email'
            return (today + timedelta(days=delta)).isoformat()

    day = month = None
    m = re.search(r"\b(?:ngày|ngay)\s*0*(\d{1,2})"                  # ngày D [tháng M]
                  r"(?:\s*(?:tháng|thang|[/.\-])\s*0*(\d{1,2}))?", low)
    if m:
        day, month = int(m[1]), (int(m[2]) if m[2] else None)
    if day is None:                                                 # D tháng M
        m = re.search(r"\b0*(\d{1,2})\s*(?:tháng|thang)\s*0*(\d{1,2})\b", low)
        if m:
            day, month = int(m[1]), int(m[2])
    if day is None:                                                 # d/m[/y] · d-m · d.m
        m = re.search(r"\b0*(\d{1,2})\s*[/.\-]\s*0*(\d{1,2})"
                      r"(?:\s*[/.\-]\s*(\d{2,4}))?\b", low)
        if m:
            day, month = int(m[1]), int(m[2])
            if m[3]:
                yr = int(m[3])
                yr = yr + 2000 if yr < 100 else yr
                for d_, m_ in ((day, month), (month, day)):         # thử cả d/m lẫn m/d
                    try:
                        return date(yr, m_, d_).isoformat()
                    except ValueError:
                        continue
                return None
    if day is None and allow_bare_day:                              # số trần (chỉ khi đang hỏi ngày)
        m = re.fullmatch(r"\s*0*(\d{1,2})\s*", low)
        if m:
            day = int(m[1])

    if day is None or not (1 <= day <= 31):
        return None
    if month is not None and not (1 <= month <= 12):
        return None
    return _resolve_next_date(day, month, today)


def _resolve_next_date(day: int, month: int | None, today: date) -> str | None:
    """Ngày gần nhất >= `today` khớp (day[, month]). Dò tối đa ~14 tháng rồi bó tay (None)."""
    def make(y: int, m: int) -> date | None:
        try:
            return date(y, m, day)
        except ValueError:                # vd 31 ở tháng chỉ có 30 ngày
            return None

    if month is not None:
        for y in (today.year, today.year + 1):
            d = make(y, month)
            if d and d >= today:
                return d.isoformat()
        return None

    y, m = today.year, today.month
    for _ in range(15):
        d = make(y, m)
        if d and d >= today:
            return d.isoformat()
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return None


# Khách từ chối / bỏ qua bước hiện tại ("không thêm gì", "thôi"). Dùng ở bước ADDON để
# sang người kế, và ở THERAPIST để bỏ chỉ định.
_NEGATIVE_WORDS = ("không", "khong", "thôi", "thoi", "bỏ qua", "bo qua", "miễn", "mien")
_NEGATIVE_EXACT = {"không", "khong", "ko", "k", "thôi", "thoi"}


# Khách gom cả danh sách ("cho tôi tất cả", "lấy hết đi", "cả 4 cái"). Chỉ dùng ở bước
# ADDON — nơi duy nhất chọn được nhiều mục.
_ALL_WORDS = ("tất cả", "tat ca", "toàn bộ", "toan bo", "hết", "het luôn", "trọn gói",
              "cả bộ", "full", "lấy hết", "lay het", "thêm hết", "them het",
              "cả ba", "cả hai", "cả bốn", "cả 3 thứ", "hết cả")

# Dạng "cả N cái" / "cả 3 món" / "cả 2" — comment gốc của _ALL_WORDS đã nêu ví dụ này
# nhưng bảng chữ lại không có, nên "Cả 3 cái" rơi xuống nhánh SỐ THỨ TỰ và bị hiểu thành
# "mục số 3": khách xin 3 add-on, hệ thống ghi 1, KHÔNG báo gì. Bắt bằng regex vì N thay đổi.
_ALL_N_RE = re.compile(r"\bcả\s*(\d{1,2})\b")


def is_select_all(text: str, n_items: int | None = None) -> bool:
    """`n_items`: số mục đang mời. Có nó thì "cả 3 cái" chỉ được coi là 'lấy hết' khi danh
    sách đúng 3 mục — "cả 2 cái" trong danh sách 3 mục là khách chọn 2 cái nào đó, không
    phải lấy hết, và ta không đoán bừa."""
    low = (text or "").strip().lower()
    if any(w in low for w in _ALL_WORDS):
        return True
    m = _ALL_N_RE.search(low)
    if not m:
        return False
    return n_items is None or int(m.group(1)) == n_items


# "Không chỉ định ai" ở bước THERAPIST. Bảng cũ chỉ có 3 cụm và THIẾU đúng chữ mà chính bot
# dùng để mời ("hay để cửa hàng tự sắp?") — khách trả lời lặp lại lời mời thì bot không
# hiểu và hỏi lại y hệt, tạo vòng lặp (log lượt 8).
_NO_PREFERENCE_RE = re.compile(
    r"(không chỉ định|khong chi dinh|không cần chỉ định|ai cũng được|ai cung duoc|bất kỳ|"
    r"bat ky|tự sắp|tu sap|tự xếp|cửa hàng sắp|cửa hàng xếp|shop sắp|shop xếp|"
    r"tùy (?:shop|cửa hàng|bên|quán)|tuy (?:shop|cua hang)|sao cũng được|sao cung duoc|"
    r"thế nào cũng được|người nào cũng được|ai trống|ai rảnh)"
)


def is_no_preference(text: str) -> bool:
    """Khách nói 'khỏi chỉ định, để cửa hàng sắp'. Dùng ở cả nhánh rule-based lẫn
    _match_therapist — LLM cũng hay trả nguyên câu đó vào ô `therapist` như thể là TÊN
    người, rồi khớp tên thất bại và bot hỏi lại."""
    return bool(_NO_PREFERENCE_RE.search((text or "").strip().lower()))


def is_negative(text: str) -> bool:
    """Câu mang ý 'không / bỏ qua'. Bắt cả câu ngắn trần ('không', 'ko') lẫn cụm trong câu."""
    low = (text or "").strip().lower().strip(".!? ")
    if low in _NEGATIVE_EXACT:
        return True
    return any(w in low for w in _NEGATIVE_WORDS)


# Khách nói muốn đổi phần nào của lịch đã đặt (UC-02) — thay cho menu nút "đổi gì" cũ.
_MODIFY_TARGETS = (
    ("keep",   ("giữ nguyên", "giu nguyen", "thôi không đổi", "thoi khong doi")),
    ("slot",   ("giờ", "gio", "thời gian", "thoi gian")),
    ("party",  ("số người", "so nguoi", "mấy người", "may nguoi")),
    ("course", ("dịch vụ", "dich vu", "gói", "goi", "course")),
)


# Đòi ĐỔI CỬA HÀNG. Phải bắt bằng Ý ĐỊNH chứ không qua tên: nhánh rule-based không biết
# tên cửa hàng (tên đến từ API), mà đây lại đúng là lúc khách hay muốn đổi — cửa hàng hiện
# tại không phục vụ được nhóm.
_CHANGE_SHOP_WORDS = ("cửa hàng khác", "cua hang khac", "quán khác", "shop khác",
                      "đổi cửa hàng", "doi cua hang", "chuyển cửa hàng", "đổi shop",
                      "đổi sang cửa hàng", "chi nhánh khác")


def is_change_shop_request(text: str) -> bool:
    low = (text or "").strip().lower()
    return any(w in low for w in _CHANGE_SHOP_WORDS)


def is_cancel_request(text: str) -> bool:
    """Câu đòi HỦY lịch. Xét trước detect_modify_target vì 'hủy lịch' cũng là một lựa chọn
    trong menu 'đổi gì' — không tách ra thì bị hiểu thành đổi nhầm mục."""
    low = (text or "").strip().lower()
    return any(w in low for w in _CANCEL_WORDS)


def detect_modify_target(text: str) -> str | None:
    """'đổi giờ' -> 'slot'; 'đổi số người' -> 'party'; 'đổi dịch vụ' -> 'course';
    'giữ nguyên' -> 'keep'. Không rõ -> None (bot hỏi lại).

    'keep' xét TRƯỚC để 'thôi không đổi giờ nữa' không bị bắt thành 'slot'."""
    low = (text or "").strip().lower()
    for target, words in _MODIFY_TARGETS:
        if any(w in low for w in words):
            return target
    return None


# --------------------------------------------------------------------------- #
#  Internals                                                                   #
# --------------------------------------------------------------------------- #

def _parse_json(raw: str):
    raw = (raw or "").strip()
    # Router hay bọc ```json ... ``` -> lấy khối {...} đầu tiên.
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


# Buổi trong ngày. _EVENING_WORDS dùng để suy "7h tối" = 19:00; cả hai bộ dùng cho
# has_daypart() — biết khách CÓ nêu buổi hay không.
_EVENING_WORDS = ("tối", "toi", "chiều", "chieu", "đêm", "dem")
_MORNING_WORDS = ("sáng", "sang", "trưa", "trua")


def has_daypart(text: str) -> bool:
    """Câu có nêu BUỔI không. Dùng ở tủ tra cứu: '7h' trần là mơ hồ (7h sáng hay 7h tối)
    nên phải trả lời cả hai; '7h tối' thì không."""
    low = (text or "").lower()
    return any(re.search(rf"\b{w}\b", low) for w in _EVENING_WORDS + _MORNING_WORDS)


# Nhận diện CÂU HỎI thông tin ở nhánh rule-based (chạy khi chưa cấu hình LLM hoặc router
# hỏng). Cụm từ cố ý mang dạng CÂU HỎI ("có làm không") chứ không phải từ trần ("chủ nhật")
# — "đặt chủ nhật này" là điền đơn, không phải hỏi. Thứ tự xét có ý nghĩa: câu hỏi vị trí
# thường chứa luôn "ở đâu" nên shops_near phải đứng trước shop_contact.
_ASK_RULES = (
    ("shops_near",    ("gần đây", "gần nhất", "gần nhà", "gần chỗ", "quanh đây",
                       "nhà tôi ở", "tôi ở")),
    # Cụm phải gắn với ĐỒNG HỒ ("mở lúc"), đừng thêm "có mở" trần — "chủ nhật có mở không"
    # là hỏi ngày nghỉ, mà shops_open_at lại xét trước shop_days_off.
    ("shops_open_at", ("còn mở", "mở cửa", "đóng cửa", "còn làm", "mở lúc", "mở vào",
                       "nào mở", "mở tới", "mở đến", "làm tới", "làm đến", "mấy giờ")),
    ("shop_days_off", ("có làm không", "có mở không", "có nghỉ không", "ngày nghỉ",
                       "nghỉ ngày nào", "nghỉ hôm nào")),
    # "phí" TRẦN quá rộng: nó nuốt luôn "hủy lịch có mất phí không" — câu hỏi CHÍNH SÁCH
    # phổ biến nhất — thành câu hỏi giá, và bot đáp lại bằng bảng giá. Chỉ nhận cụm chỉ
    # giá rõ ràng; phần còn lại để lưới FAQ hứng.
    ("course_price",  ("bao nhiêu tiền", "giá bao nhiêu", "giá thế nào", "giá là",
                       "mất bao nhiêu", "chi phí", "bảng giá")),
    ("shop_contact",  ("ở đâu", "địa chỉ", "số điện thoại", "sđt", "số liên hệ")),
    ("shops_by_staff", ("nữ phục vụ", "nam phục vụ", "nhân viên nữ", "nhân viên nam",
                        "bao nhiêu nhân viên", "mấy nhân viên", "nhân viên trực",
                        "đủ nhân viên", "nữ trực", "nam trực")),
)

# "cửa hàng nào" quá chung nên xét CUỐI CÙNG, sau cả lưới "có nhắc giờ" — nếu không,
# "Còn cửa hàng nào lúc 15:00 không?" sẽ bị hiểu thành hỏi danh sách.
_ASK_LIST_WORDS = ("cửa hàng nào", "cua hang nao", "những cửa hàng", "các cửa hàng",
                   "danh sách cửa hàng", "bao nhiêu cửa hàng", "mấy cửa hàng")


# Từ để hỏi. Khách TRẢ LỜI câu bot hỏi ("Hải Châu", "Gói đầu tiên", "massage body 30") gần như
# không bao giờ chứa mấy từ này, còn câu hỏi thật thì hầu như luôn có (kể cả khi quên "?":
# "Cửa hàng nào mở lúc 7h tối nay.").
_QUESTION_WORDS = ("nào", "đâu", "bao nhiêu", "mấy giờ", "mấy tiếng", "khi nào", "thế nào",
                   "ra sao", "có phải", "được không", "cho hỏi", "cho em hỏi",
                   # Dạng ĐỀ NGHỊ: khách không đặt câu hỏi mà nhờ tra cứu ("tôi muốn tìm
                   # cửa hàng có 3 nữ phục vụ") — vẫn là hỏi thông tin.
                   "muốn tìm", "tìm giúp", "tìm cho", "kiếm giúp", "kiếm cho",
                   "gợi ý", "tư vấn", "cho tôi biết", "cho em biết", "có những")


def looks_like_question(text: str) -> bool:
    low = (text or "").strip().lower()
    return "?" in low or any(w in low for w in _QUESTION_WORDS)


_CLOCK_RE = re.compile(r"\b\d{1,2}\s*(?:h|:|giờ|gio)")
_SHOP_WORDS = ("cửa hàng", "cua hang", "shop", "quán", "quan", "chi nhánh")


def _detect_question(low: str) -> str | None:
    for qt, words in _ASK_RULES:
        if any(w in low for w in words):
            return qt
    # Lưới cuối: hỏi về CỬA HÀNG + có nêu GIỜ nhưng không dùng chữ "mở" nào trong bảng
    # ("Còn cửa hàng nào lúc 15:00 không?") -> vẫn là hỏi giờ mở cửa.
    if any(w in low for w in _SHOP_WORDS) and _CLOCK_RE.search(low):
        return "shops_open_at"
    if any(w in low for w in _ASK_LIST_WORDS):
        return "shops_list"
    return None


# Phải là ĐÒI GẶP NGƯỜI THẬT. Trước đây để "nhân viên" trần nên "cửa hàng nào có 2 nhân
# viên nam trực?" cũng bị đẩy sang handoff — khách hỏi thông tin lại bị mời gọi điện.
_HANDOFF_WORDS = ("gặp nhân viên", "gap nhan vien", "gặp người", "người thật", "nguoi that",
                  "nói chuyện với", "noi chuyen voi", "gọi cửa hàng", "goi cua hang",
                  "tổng đài", "tong dai", "nhân viên tư vấn", "nhân viên hỗ trợ")
# "cancel"/"ok"/"oke" giữ lại: từ mượn khách Việt vẫn hay gõ.
_CANCEL_WORDS = ("hủy", "huỷ", "cancel")
_MODIFY_WORDS = ("sửa", "đổi lịch", "thay đổi")
_YES_WORDS = ("đồng ý", "xác nhận", "đúng rồi", "chốt", "vâng", "ok", "oke")
_NO_WORDS = ("không phải", "sai rồi", "chưa đúng")


def _has_word(low: str, words) -> bool:
    """Khớp theo RANH GIỚI TỪ, không phải chuỗi con.

    Lý do: 'hủy' nằm gọn trong 'Thủy' — tên người và tên địa danh (Thủy Nguyên) đều rất
    phổ biến. Khớp chuỗi con khiến mọi câu nhắc tới Thủy đều thành intent=cancel; ở bước
    CONFIRM thì câu "đổi sang Cửa hàng Thủy Nguyên" (ý muốn ĐỔI) hoá ra là lệnh hủy. Cùng
    loại lỗi: 'ok' nằm trong tên riêng viết không dấu.

    `\\b` của Python hiểu chữ Unicode nên chạy đúng với tiếng Việt có dấu."""
    return any(re.search(rf"\b{re.escape(w)}\b", low) for w in words)


def _rule_based(text: str) -> dict:
    """Trích param offline khi chưa cấu hình router. Phủ luồng chính; không thay LLM thật."""
    low = text.lower()
    question_type = _detect_question(low)
    intent = "book"
    if any(w in low for w in _HANDOFF_WORDS):
        intent = "handoff"
        question_type = None
    elif question_type:
        intent = "ask_info"
    elif _has_word(low, _CANCEL_WORDS):
        intent = "cancel"
    elif _has_word(low, _MODIFY_WORDS):
        intent = "modify"

    entities = {k: None for k in _ENTITY_KEYS}
    entities["addons"] = []

    # date — ISO/tương đối/'d/m'/'ngày D tháng M'. KHÔNG lấy số trần ở đây (NLU không biết
    # bước hiện tại; số trần diễn giải theo ngữ cảnh ở orchestrator bước DATE).
    entities["date"] = parse_date_freeform(low, allow_bare_day=False)

    # time: "8:00", "8h", "8 giờ", "14:30", "7h tối"
    m = re.search(r"\b(\d{1,2})(?::|h|\s*giờ)(\d{2})?\b", low)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        # "7h tối" = 19:00 chứ không phải 07:00 — buổi nói sau giờ nên regex trên không thấy.
        if hh < 12 and any(re.search(rf"\b{w}\b", low) for w in _EVENING_WORDS):
            hh += 12
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            entities["time"] = f"{hh:02d}:{mm:02d}"

    # party_size
    m = re.search(r"\b(\d+)\s*(?:người|nguoi|ng)\b", low)
    if m:
        entities["party_size"] = int(m.group(1))

    # duration
    m = re.search(r"\b(\d+)\s*(?:phút|phut)\b", low)
    if m:
        entities["duration"] = int(m.group(1))

    # therapist
    if re.search(r"\b(nữ|nu)\b", low):
        entities["therapist"] = "female"
    elif re.search(r"\b(nam)\b", low):
        entities["therapist"] = "male"
    elif _NO_PREFERENCE_RE.search(low):
        entities["therapist"] = "none"

    # confirm — _has_word chứ KHÔNG phải `in`: xem docstring _has_word ('ok' ⊂ 'Sài Gòn').
    if any(w in low for w in _NO_WORDS):
        entities["confirm"] = "no"
    elif _has_word(low, _YES_WORDS):
        entities["confirm"] = "yes"

    return {"intent": intent, "entities": entities, "question_type": question_type}
