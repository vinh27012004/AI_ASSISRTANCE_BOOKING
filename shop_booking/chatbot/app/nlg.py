"""NLG (bước ⑤⑥) — DD §3.1, chatbot-architecture.md §1/§10.

⑤ build_prompt: code ghép instruction[state] + facts + options + lang (facts lấy TỪ slots/
   api_result — mọi số liệu từ đây, LLM không được bịa §10).
⑥ generate: LLM diễn đạt tự nhiên; không có router thì dùng câu mẫu offline (templates.FAKE).

Câu bot vẫn ở dạng ĐÃ MASK (chứa {{phone_1}}…); orchestrator unmask ở cuối trước khi trả
widget của chính khách.
"""

from __future__ import annotations

import json

from app import templates
from app.llm_client import LLMError, RealLLMClient
from app.session import Session

_NLG_SYSTEM = (
    "Bạn là trợ lý đặt lịch massage, nói chuyện lịch sự, ngắn gọn. Diễn đạt TỰ NHIÊN bằng "
    "ngôn ngữ 'lang'. CHỈ dùng dữ kiện trong 'facts'; TUYỆT ĐỐI KHÔNG bịa giá, dịch vụ hay "
    "khung giờ không có.\n"
    "Các lựa chọn cho khách (cửa hàng, course, giờ...) ĐÃ được hiển thị bằng NÚT bên dưới câu "
    "trả lời — vì vậy CHỈ hỏi ngắn gọn, KHÔNG liệt kê lại danh sách trong câu.\n"
    "TUYỆT ĐỐI KHÔNG tự tạo chỗ trống dạng {{...}} (không viết {{buttons}}, {{courses}}, "
    "{{slots}}...). Chỉ khi trong 'facts' CÓ SẴN placeholder PII như {{phone_1}} thì giữ NGUYÊN "
    "VĂN nó. Trả về DUY NHẤT câu trả lời cho khách, không kèm giải thích."
)


def build_prompt(state_key: str, session: Session, api_result: dict, lang: str) -> dict:
    """Trả prompt có cấu trúc cho bước ⑥. `state_key` có thể là state hoặc nhánh đặc biệt
    (REPROMPT/HANDOFF/END/ERROR)."""
    facts = _facts_for(state_key, session, api_result)
    return {
        "key": state_key,
        "lang": lang,
        "instruction": templates.INSTRUCTION.get(state_key, templates.INSTRUCTION["REPROMPT"]),
        "facts": facts,
    }


def generate(prompt: dict, llm: RealLLMClient | None) -> str:
    """Sinh câu. Không router -> câu mẫu offline theo state × lang."""
    if llm is None:
        return templates.fake_sentence(prompt["key"], prompt["lang"], prompt["facts"])
    try:
        user = json.dumps(
            {k: prompt[k] for k in ("lang", "instruction", "facts")},
            ensure_ascii=False,
        )
        text = llm.complete(_NLG_SYSTEM, user, temperature=0.4, max_tokens=400)
        return text.strip() or templates.fake_sentence(prompt["key"], prompt["lang"], prompt["facts"])
    except LLMError:
        return templates.fake_sentence(prompt["key"], prompt["lang"], prompt["facts"])


# --------------------------------------------------------------------------- #
#  Facts                                                                       #
# --------------------------------------------------------------------------- #

def _facts_for(state_key: str, session: Session, api_result: dict) -> dict:
    s = session.slots
    ar = api_result or {}
    facts: dict = {}

    # Đưa lựa chọn THẬT vào facts (LLM có dữ liệu chính xác, khỏi bịa placeholder/giá).
    if state_key == "SHOP":
        facts["cua_hang"] = [sh.get("name") for sh in ar.get("shops", [])]
    elif state_key == "COURSE":
        facts["course"] = [
            f'{c.get("name")} · {c.get("duration_min")} phút · {c.get("price")}¥'
            for c in ar.get("courses", [])
        ]
    elif state_key == "ADDON":
        facts["add_on"] = [
            f'{a.get("name")} · {a.get("duration_min")} phút · {a.get("price")}¥'
            for a in ar.get("addons", [])
        ]
    elif state_key == "THERAPIST":
        facts["nhan_vien"] = [
            f'{t.get("name")} ({"nữ" if t.get("gender") == "female" else "nam"})'
            for t in ar.get("therapists", [])
        ]
    elif state_key == "CONTACT":
        facts["hoi"] = _contact_ask(session)      # CHỈ những gì còn thiếu (phone/email)

    if state_key == "SLOT":
        times = ar.get("slots") or ar.get("suggested_slots") or []
        facts["gio_trong"] = times
        facts["slots"] = ", ".join(times) if times else "(chưa có)"
    elif state_key == "CONFIRM":
        facts["summary"] = _order_summary(session, ar)
    elif state_key in ("DONE", "UPDATED", "CANCELLED"):
        facts["booking_code"] = session.booking_code or ""
    elif state_key in ("END", "HANDOFF", "ERROR"):
        facts["message"] = ar.get("message", "")
        facts["shop_phone"] = ar.get("shop_phone") or session.shop_phone or ""
    return facts


# Nhãn phone/email + từ nối theo ngôn ngữ, để câu hỏi CONTACT chỉ nhắc phần còn thiếu.
_CONTACT_LABELS = {
    "vi": ("số điện thoại", "email", " và "),
    "en": ("phone number", "email", " and "),
    "ja": ("電話番号", "メールアドレス", "と"),
}


def _contact_ask(session: Session) -> str:
    """Chuỗi thông tin CÒN THIẾU ở CONTACT — đã có số thì chỉ hỏi email, và ngược lại."""
    s = session.slots
    lang = session.lang if session.lang in _CONTACT_LABELS else "vi"
    phone_l, email_l, join = _CONTACT_LABELS[lang]
    missing = []
    if not s.phone:
        missing.append(phone_l)
    if not s.email:
        missing.append(email_l)
    if not missing:                              # cả hai đã có -> nêu chung (hiếm khi tới đây)
        missing = [phone_l, email_l]
    return join.join(missing)


def _order_summary(session: Session, api_result: dict) -> str:
    """Đọc lại đơn ở CONFIRM. Dùng tên course/giờ; SĐT/email để placeholder (unmask ở cuối)."""
    s = session.slots
    parts = []
    if s.date:
        parts.append(f"ngày {s.date}")
    if s.slot:
        parts.append(f"lúc {s.slot}")
    if s.party_size:
        parts.append(f"{s.party_size} người")
    course_name = s.course_name or (api_result or {}).get("course_name")
    if course_name:
        parts.append(f"gói {course_name}")
    elif s.duration:
        parts.append(f"{s.duration} phút")
    if s.addons:
        parts.append(f"+{len(s.addons)} dịch vụ thêm")
    if s.therapist_gender:
        parts.append("nhân viên " + ("nữ" if s.therapist_gender == "female" else "nam"))
    elif s.therapist_id:
        parts.append("nhân viên đã chỉ định")
    if s.phone:
        parts.append(f"SĐT {s.phone}")   # placeholder -> unmask ở orchestrator
    return ", ".join(parts)
