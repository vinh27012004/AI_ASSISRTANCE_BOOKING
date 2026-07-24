"""NLU (bước ①) — DD §2.3/§3.4, chatbot-architecture.md §3.4, Q4.

LLM chỉ TRÍCH param -> JSON cố định, KHÔNG trả lời. Code validate JSON trước khi merge
(sai schema -> coi như không trích được -> hỏi lại). Đây là ranh giới "LLM hiểu" ↔ "code
quyết" và là chỗ chống prompt injection tầng client.

Không cấu hình router (llm=None) -> nhánh rule-based offline: đủ cho dev/test luồng chính.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.llm_client import LLMError, RealLLMClient

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
    else:
        try:
            today = date.today()
            raw = llm.complete(
                _NLU_SYSTEM,
                f"[lang={lang}] [Hôm nay={today.isoformat()} ({today:%A})] {masked_text}",
                temperature=0.0, max_tokens=400, response_json=True,
            )
            parsed = validate_schema(_parse_json(raw))
        except LLMError:
            # Router lỗi -> đừng để rơi cả lượt: thử rule-based rồi mới bó tay.
            parsed = _rule_based(masked_text)

    if parsed is None:
        return None
    parsed["entities"] = _normalize_entities(parsed["entities"])  # date tương đối -> ISO
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
    """Lưới an toàn: nếu LLM vẫn trả date tương đối ('tomorrow'/'mai'…) thay vì ISO,
    quy về YYYY-MM-DD ở đây; không quy được thì bỏ (None) để state machine hỏi lại —
    thà hỏi lại còn hơn để 'tomorrow' lọt vào slots.date rồi shop_api báo lỗi."""
    d = entities.get("date")
    if d:
        entities["date"] = _to_iso_date(d)
    return entities


_REL_TODAY = {"today", "hôm nay", "hom nay", "nay", "今日", "本日", "きょう"}
_REL_TOMORROW = {"tomorrow", "ngày mai", "ngay mai", "mai", "明日", "あした", "あす"}
_REL_DAY_AFTER = {"day after tomorrow", "ngày kia", "ngay kia", "mốt", "mot", "明後日"}


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

    # date
    if re.search(r"\b(hôm nay|today|今日)\b", low):
        entities["date"] = date.today().isoformat()
    elif re.search(r"\b(ngày mai|mai|tomorrow|明日|あした)\b", low):
        entities["date"] = (date.today() + timedelta(days=1)).isoformat()
    else:
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", low)
        if m:
            entities["date"] = m.group(1)

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
