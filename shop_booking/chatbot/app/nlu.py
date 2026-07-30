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
_ENTITY_KEYS = ("date", "time", "party_size", "duration", "course", "addons", "therapist", "confirm")

_NLU_SYSTEM = (
    "Bạn là bộ trích xuất tham số cho hệ thống đặt lịch massage. CHỈ trích xuất, TUYỆT ĐỐI "
    "KHÔNG trả lời khách. Trả về DUY NHẤT một JSON đúng schema:\n"
    '{"intent":"book|modify|cancel|ask_info|chitchat|handoff",'
    '"entities":{"date":"YYYY-MM-DD|null","time":"HH:MM|null","party_size":"1|null",'
    '"duration":"60|null","course":"text|null","addons":[],'
    '"therapist":"name|male|female|none|null","confirm":"yes|no|null"}}\n'
    "QUAN TRỌNG: date PHẢI là ngày tuyệt đối YYYY-MM-DD. Khách nói tương đối (hôm nay/mai/"
    "ngày kia/thứ Hai tuần sau) thì tự quy đổi dựa trên 'Hôm nay' được cung cấp; time là 24h "
    "HH:MM. Không thêm chữ nào ngoài JSON. Không suy diễn giá trị khách không nói (để null)."
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


_REL_TODAY = {"today", "hôm nay", "hom nay", "nay", "今日", "本日", "きょう"}
_REL_TOMORROW = {"tomorrow", "ngày mai", "ngay mai", "mai", "明日", "あした", "あす"}
_REL_DAY_AFTER = {"day after tomorrow", "ngày kia", "ngay kia", "mốt", "mot", "明後日"}

_CJK_RE = re.compile(r"[぀-ヿ一-鿿]")


def _match_rel(low: str, words: set[str]) -> bool:
    """Khớp từ chỉ ngày tương đối. Từ Latin/Việt cần RANH GIỚI TỪ (\\b) để 'mai' không dính
    trong 'email', 'nay' không dính trong 'ngày'…; từ Nhật (CJK không có ranh giới) dùng
    chuỗi con."""
    for w in words:
        if _CJK_RE.search(w):
            if w in low:
                return True
        elif re.search(rf"\b{re.escape(w)}\b", low):
            return True
    return False


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
    m = re.search(r"(?:(\d{1,2})\s*月\s*)?(\d{1,2})\s*日", low)      # (M月)?D日
    if m:
        month = int(m[1]) if m[1] else None
        day = int(m[2])
    if day is None:                                                 # ngày D [tháng M] / day D
        m = re.search(r"\b(?:ngày|ngay|day)\s*0*(\d{1,2})"
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


def detect_lang(text: str) -> str | None:
    """Nhận diện ngôn ngữ từ tin nhắn (§7). Nhật > Việt (dấu) > Anh. None -> giữ nguyên
    ngôn ngữ đang dùng.

    BỎ email /   SĐT / mã / placeholder trước khi đoán: chữ Latin trong email hay mã KHÔNG phải
    tín hiệu tiếng Anh — trước đây khách gõ 'sđt + email' làm bot nhảy sang tiếng Anh (kể cả
    câu chặn NG)."""
    cleaned = _EMAIL_STRIP.sub(" ", text)
    cleaned = _PLACEHOLDER_STRIP.sub(" ", cleaned)
    cleaned = _LONGNUM_STRIP.sub(" ", cleaned)
    if re.search(r"[぀-ヿ一-鿿]", cleaned):   # kana + kanji
        return "ja"
    if re.search(r"[ăâđêôơưàáạảãèéẹẻẽìíịỉĩòóọỏõùúụủũỳýỵỷỹ]", cleaned, re.IGNORECASE):
        return "vi"
    if re.search(r"[a-zA-Z]", cleaned):
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
                  "agent", "human", "staff", "スタッフ", "オペレーター")
_CANCEL_WORDS = ("hủy", "huỷ", "cancel", "キャンセル")
_MODIFY_WORDS = ("sửa", "đổi lịch", "thay đổi", "reschedule", "変更")
_YES_WORDS = ("đồng ý", "xác nhận", "đúng rồi", "chốt", "vâng", "ok", "oke", "yes",
              "correct", "confirm", "はい", "確認")
_NO_WORDS = ("không phải", "sai rồi", "chưa đúng", "no", "not", "いいえ")


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

    # date — ISO/tương đối/'d/m'/'ngày D tháng M'/'D日'. KHÔNG lấy số trần ở đây (NLU
    # không biết bước hiện tại; số trần diễn giải theo ngữ cảnh ở orchestrator bước DATE).
    entities["date"] = parse_date_freeform(low, allow_bare_day=False)

    # time: "8:00", "8h", "8 giờ", "14:30"
    m = re.search(r"\b(\d{1,2})(?::|h|時|\s*giờ)(\d{2})?\b", low)
    if m:
        hh = int(m.group(1))
        mm = int(m.group(2)) if m.group(2) else 0
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            entities["time"] = f"{hh:02d}:{mm:02d}"

    # party_size
    m = re.search(r"\b(\d+)\s*(?:người|ng|people|person|名|人)\b", low)
    if m:
        entities["party_size"] = int(m.group(1))

    # duration
    m = re.search(r"\b(\d+)\s*(?:phút|phut|min|minutes|分)\b", low)
    if m:
        entities["duration"] = int(m.group(1))

    # therapist
    if re.search(r"\b(nữ|nu|female|女性)\b", low):
        entities["therapist"] = "female"
    elif re.search(r"\b(nam|male|男性)\b", low):
        entities["therapist"] = "male"
    elif re.search(r"(không chỉ định|ai cũng được|bất kỳ|skip|no preference|誰でも)", low):
        entities["therapist"] = "none"

    # confirm
    if any(w in low for w in _NO_WORDS):
        entities["confirm"] = "no"
    elif any(w in low for w in _YES_WORDS):
        entities["confirm"] = "yes"

    return {"intent": intent, "entities": entities}
