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
from datetime import date, timedelta

from app.llm_client import LLMError, RealLLMClient

logger = logging.getLogger(__name__)

INTENTS = {"book", "modify", "cancel", "ask_info", "chitchat", "handoff"}
_ENTITY_KEYS = ("shop", "date", "time", "party_size", "duration", "course", "addons",
                "therapist", "confirm")

_NLU_SYSTEM = (
    "Bạn là bộ trích xuất tham số cho hệ thống đặt lịch massage. CHỈ trích xuất, TUYỆT ĐỐI "
    "KHÔNG trả lời khách. Trả về DUY NHẤT một JSON đúng schema:\n"
    '{"intent":"book|modify|cancel|ask_info|chitchat|handoff",'
    '"entities":{"shop":"text|null","date":"YYYY-MM-DD|null","time":"HH:MM|null",'
    '"party_size":"1|null","duration":"60|null","course":"text|null","addons":[],'
    '"therapist":"name|male|female|none|null","confirm":"yes|no|null"}}\n'
    "QUAN TRỌNG: date PHẢI là ngày tuyệt đối YYYY-MM-DD. Khách nói tương đối (hôm nay/mai/"
    "ngày kia/thứ Hai tuần sau) thì tự quy đổi dựa trên 'Hôm nay' được cung cấp; time là 24h "
    "HH:MM. shop LÀ TÊN/ĐỊA ĐIỂM cửa hàng khách nêu (vd 'Sendai', 'Tokyo', 'chi nhánh Osaka') — "
    "chỉ trích khi khách CHỈ RÕ cửa hàng, không suy diễn. Không thêm chữ nào ngoài JSON. "
    "Không suy diễn giá trị khách không nói (để null)."
)


# --------------------------------------------------------------------------- #
#  Public                                                                      #
# --------------------------------------------------------------------------- #

def extract(masked_text: str, lang: str, llm: RealLLMClient | None) -> dict | None:
    """Trả {'intent', 'entities'} đã validate, hoặc None nếu không trích được (hỏi lại)."""
    if llm is None:
        parsed = _rule_based(masked_text)
        source = "rule_based (chưa cấu hình LLM)"
    else:
        parsed = None
        try:
            today = date.today()
            raw = llm.complete(
                _NLU_SYSTEM,
                f"[lang={lang}] [Hôm nay={today.isoformat()} ({today:%A})] {masked_text}",
                temperature=0.0, max_tokens=400, response_json=True,
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
        logger.warning("nlu: không trích được gì từ %r (lang=%s) -> hỏi lại", masked_text, lang)
        return None
    parsed["entities"] = _normalize_entities(parsed["entities"])  # date tương đối -> ISO
    logger.info("nlu: text=%r lang=%s source=%s -> intent=%s entities=%s",
                masked_text, lang, source, parsed["intent"], parsed["entities"])
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
    return {"intent": intent, "entities": entities}


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


_REL_TODAY = {"today", "hôm nay", "hom nay", "nay"}
_REL_TOMORROW = {"tomorrow", "ngày mai", "ngay mai", "mai"}
_REL_DAY_AFTER = {"day after tomorrow", "ngày kia", "ngay kia", "mốt", "mot"}


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
    'ngày D [tháng M]' · 'D tháng M' · 'day D'; kiểu Nhật '(M月)?D日'. Khi `allow_bare_day`
    thì hiểu cả SỐ TRẦN 'D' — chỉ bật khi ĐANG hỏi ngày, để '3' ở bước khác không bị hiểu
    nhầm là ngày mùng 3.

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
    m = re.search(r"\b(?:ngày|ngay|day)\s*0*(\d{1,2})"              # ngày D [tháng M] / day D
                  r"(?:\s*(?:tháng|thang|month|[/.\-])\s*0*(\d{1,2}))?", low)
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


_EMAIL_STRIP = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PLACEHOLDER_STRIP = re.compile(r"\{\{[^}]+\}\}")
_LONGNUM_STRIP = re.compile(r"\d[\d\-.\s]{5,}\d")


# Khách CHỌN ngôn ngữ bằng lời — cần cho kênh gọi điện (không gõ/bấm gì được). Chỉ khớp
# khi CẢ câu là tên ngôn ngữ, để "english massage" không bị hiểu nhầm thành chọn tiếng Anh.
_LANG_CHOICE = {
    "vi": {"tiếng việt", "tieng viet", "vietnamese"},
    "en": {"english", "tiếng anh", "tieng anh"},
}


def detect_lang_choice(text: str) -> str | None:
    """'Tiếng Việt'/'English' -> mã lang; câu khác -> None."""
    low = (text or "").strip().lower().strip(".!? ")
    for lang, phrases in _LANG_CHOICE.items():
        if low in phrases:
            return lang
    return None


# Khách từ chối / bỏ qua bước hiện tại ("không thêm gì", "thôi", "no thanks"). Dùng ở
# bước ADDON để sang người kế, và ở THERAPIST để bỏ chỉ định.
_NEGATIVE_WORDS = ("không", "khong", "thôi", "thoi", "bỏ qua", "bo qua", "miễn", "mien",
                   "no thanks", "no thank", "nothing", "none", "no add", "skip", "nope")
_NEGATIVE_EXACT = {"không", "khong", "no", "ko", "k", "thôi", "thoi", "nope", "none", "skip"}


def is_negative(text: str) -> bool:
    """Câu mang ý 'không / bỏ qua'. Bắt cả câu ngắn trần ('không', 'no') lẫn cụm trong câu.

    'no' KHÔNG nằm trong _NEGATIVE_WORDS (chỉ khớp nguyên câu) vì nó là chuỗi con của quá
    nhiều từ ('nothing', 'không' viết liền…) — bắt rộng ở đây sẽ nuốt nhầm câu chọn add-on."""
    low = (text or "").strip().lower().strip(".!? ")
    if low in _NEGATIVE_EXACT:
        return True
    return any(w in low for w in _NEGATIVE_WORDS)


# Khách nói muốn đổi phần nào của lịch đã đặt (UC-02) — thay cho menu nút "đổi gì" cũ.
_MODIFY_TARGETS = (
    ("keep",   ("giữ nguyên", "giu nguyen", "thôi không đổi", "thoi khong doi", "keep",
                "leave it", "nothing")),
    ("slot",   ("giờ", "gio", "thời gian", "thoi gian", "time", "hour", "schedule")),
    ("party",  ("số người", "so nguoi", "mấy người", "may nguoi", "party", "people",
                "guest", "person")),
    ("course", ("dịch vụ", "dich vu", "gói", "goi", "course", "service", "package")),
)


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


# Từ khóa nhận diện ngôn ngữ khi câu KHÔNG có dấu tiếng Việt. Cố ý không đưa vào đây các
# từ trùng với DỮ LIỆU của mình (tên cửa hàng/course/add-on như "shop", "foot", "oil") —
# đó là lựa chọn khách đọc lại, không phải tín hiệu ngôn ngữ.
_VI_WORDS = {
    "toi", "minh", "muon", "can", "dat", "lich", "hen", "ngay", "gio", "phut", "nguoi",
    "khong", "duoc", "giup", "gium", "nhe", "a", "cua", "hang", "may", "bao", "nhieu",
    "roi", "chua", "luc", "vao", "cho", "voi", "va", "hay", "the", "nao", "sang", "chieu",
    "toi_nay", "mai", "mot", "hai", "ba", "bon", "sau", "bay", "tam", "chin", "muoi",
}
_EN_WORDS = {
    "i", "you", "we", "my", "me", "want", "would", "like", "need", "please", "book",
    "booking", "can", "could", "and", "for", "at", "with", "is", "are", "do", "does",
    "hello", "hi", "thanks", "thank", "yes", "no", "tomorrow", "today", "people",
    "person", "morning", "afternoon", "evening", "available", "change", "cancel",
}
_WORD_RE = re.compile(r"[a-z]+")


def detect_lang(text: str) -> str | None:
    """Nhận diện ngôn ngữ từ tin nhắn (§7). None -> GIỮ NGUYÊN ngôn ngữ đang dùng.

    BỎ email/SĐT/mã/placeholder trước khi đoán: chữ Latin trong email hay mã KHÔNG phải
    tín hiệu tiếng Anh.

    KHÔNG dùng luật cũ "có chữ Latin = tiếng Anh": tiếng Việt gõ KHÔNG DẤU
    ("toi muon dat lich") và cả giờ giấc ("16h30" — chữ 'h') đều toàn chữ Latin, nên luật
    đó làm bot lật sang tiếng Anh giữa chừng hội thoại. Nay phải có TỪ đặc trưng mới đổi;
    không đủ căn cứ thì trả None để giữ nguyên."""
    cleaned = _EMAIL_STRIP.sub(" ", text)
    cleaned = _PLACEHOLDER_STRIP.sub(" ", cleaned)
    cleaned = _LONGNUM_STRIP.sub(" ", cleaned)
    # Ạ-ỹ (Latin Extended Additional) phủ TOÀN BỘ nguyên âm dấu tổ hợp (ế ệ ố ộ
    # ớ ợ ứ ự ắ ặ ấ ậ...) — liệt kê tay trước đây sót nhóm này nên "Tiếng Việt" (có ế/ệ)
    # không khớp ký tự Việt nào, rơi xuống nhánh [a-zA-Z] và bị đoán thành "en".
    if re.search(r"[ăâđêôơưàáãèéìíòóõùúýĩũẠ-ỹ]", cleaned, re.IGNORECASE):
        return "vi"

    words = set(_WORD_RE.findall(cleaned.lower()))
    vi_hits, en_hits = len(words & _VI_WORDS), len(words & _EN_WORDS)
    if vi_hits > en_hits:
        return "vi"
    if en_hits > vi_hits:
        return "en"
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


_HANDOFF_WORDS = ("nhân viên", "người thật", "gặp người", "gọi cửa hàng", "tổng đài",
                  "agent", "human", "staff")
_CANCEL_WORDS = ("hủy", "huỷ", "cancel")
_MODIFY_WORDS = ("sửa", "đổi lịch", "thay đổi", "reschedule")
_YES_WORDS = ("đồng ý", "xác nhận", "đúng rồi", "chốt", "vâng", "ok", "oke", "yes",
              "correct", "confirm")
_NO_WORDS = ("không phải", "sai rồi", "chưa đúng", "no", "not")


def _rule_based(text: str) -> dict:
    """Trích param offline khi chưa cấu hình router. Phủ luồng chính; không thay LLM thật."""
    low = text.lower()
    intent = "book"
    if any(w in low for w in _HANDOFF_WORDS):
        intent = "handoff"
    elif any(w in low for w in _CANCEL_WORDS):
        intent = "cancel"
    elif any(w in low for w in _MODIFY_WORDS):
        intent = "modify"

    entities = {k: None for k in _ENTITY_KEYS}
    entities["addons"] = []

    # date — ISO/tương đối/'d/m'/'ngày D tháng M'. KHÔNG lấy số trần ở đây (NLU không biết
    # bước hiện tại; số trần diễn giải theo ngữ cảnh ở orchestrator bước DATE).
    entities["date"] = parse_date_freeform(low, allow_bare_day=False)

    # time: "8:00", "8h", "8 giờ", "14:30"
    m = re.search(r"\b(\d{1,2})(?::|h|\s*giờ)(\d{2})?\b", low)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            entities["time"] = f"{hh:02d}:{mm:02d}"

    # party_size
    m = re.search(r"\b(\d+)\s*(?:người|ng|people|person)\b", low)
    if m:
        entities["party_size"] = int(m.group(1))

    # duration
    m = re.search(r"\b(\d+)\s*(?:phút|phut|min|minutes)\b", low)
    if m:
        entities["duration"] = int(m.group(1))

    # therapist
    if re.search(r"\b(nữ|nu|female)\b", low):
        entities["therapist"] = "female"
    elif re.search(r"\b(nam|male)\b", low):
        entities["therapist"] = "male"
    elif re.search(r"(không chỉ định|ai cũng được|bất kỳ|skip|no preference)", low):
        entities["therapist"] = "none"

    # confirm
    if any(w in low for w in _NO_WORDS):
        entities["confirm"] = "no"
    elif any(w in low for w in _YES_WORDS):
        entities["confirm"] = "yes"

    return {"intent": intent, "entities": entities}
