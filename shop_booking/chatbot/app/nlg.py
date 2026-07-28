"""NLG (bước ⑤⑥) — DD §3.1, chatbot-architecture.md §1/§10.

⑤ build_prompt: code ghép instruction[state] + facts + options + lang (facts lấy TỪ slots/
   api_result — mọi số liệu từ đây, LLM không được bịa §10).
⑥ generate: LLM diễn đạt tự nhiên; không có router thì dùng câu mẫu offline (templates.FAKE).

Câu bot vẫn ở dạng ĐÃ MASK (chứa {{phone_1}}…); orchestrator unmask ở cuối trước khi trả
widget của chính khách.
"""

from __future__ import annotations

import json
import time

from app import pii, templates
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


# Câu chứa số/mã THẬT mà LLM có thể sửa/bịa (shop_phone, booking_code, giờ trống) -> LUÔN
# dùng template code, KHÔNG qua LLM (§10: cấm LLM tự sinh số liệu; số trong nút do code tạo,
# text phải khớp). Đa ngôn ngữ vẫn giữ vì template đã tách vi/en/ja.
# ADDON: khi đặt NHÓM, câu phải nêu ĐANG hỏi "Người n/m" (BR-10) — để LLM diễn đạt thì nó
# hay bỏ mất chỉ số này, khiến người 2 nhìn giống hỏi lại người 1. Ép template tất định.
# MODIFY: menu "đổi gì" — ép template tất định để chèn đồng hồ "sửa nhanh còn ~m:ss" (BR-17)
# chính xác, không để LLM bịa/bỏ.
_LITERAL_SAFE_KEYS = {"SLOT", "ADDON", "MODIFY", "END", "HANDOFF", "ERROR", "DONE", "UPDATED", "CANCELLED"}


def generate(prompt: dict, llm: RealLLMClient | None) -> str:
    """Sinh câu. Không router HOẶC câu chứa số/mã thật -> câu mẫu code (khớp chính xác)."""
    if llm is None or prompt["key"] in _LITERAL_SAFE_KEYS:
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
        # Câu ĐỘNG: đã chọn add-on cho người này -> XÁC NHẬN + chỉ tới nút ✅; chưa chọn ->
        # mời chọn. Tránh lặp câu y hệt sau mỗi lần chọn (khách tưởng bị treo, bấm toggle mãi).
        facts["addon_line"] = _addon_prompt_line(session, ar)
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

    # Đồng hồ "sửa nhanh còn ~m:ss" (BR-17) cho màn đặt xong / đã cập nhật / menu sửa.
    if state_key in ("DONE", "UPDATED", "MODIFY"):
        facts["sua_nhanh"] = _quick_edit_note(session)
    return facts


# (còn thời gian sửa nhanh, hết thời gian) — {t} là "m:ss" còn lại.
_QUICK_EDIT = {
    "vi": (" ⏱ Sửa/hủy nhanh (không cần nhập email) còn khoảng {t}.",
           " ⏱ Cửa sổ sửa nhanh 2 phút đã hết — sửa/hủy sẽ cần nhập lại email."),
    "en": (" ⏱ Quick edit/cancel (no email needed) for about {t} more.",
           " ⏱ The 2-minute quick-edit window has passed — editing now needs your email."),
    "ja": (" ⏱ あと約{t}、メール不要で変更・キャンセルできます。",
           " ⏱ 2分の即時変更枠は終了しました。変更にはメールの入力が必要です。"),
}


def _quick_edit_note(session: Session) -> str:
    """Nhắc cửa sổ sửa nhanh 2' (BR-17): còn giờ -> 'còn ~m:ss'; hết -> nhắc cần email.
    Chatbot không tick liên tục được nên đây là ẢNH CHỤP lúc gửi tin (cập nhật mỗi lượt)."""
    exp = session.edit_token_expires_at
    if not exp:
        return ""
    lang = session.lang if session.lang in _QUICK_EDIT else "vi"
    left = int(round(exp - time.time()))
    live, over = _QUICK_EDIT[lang]
    return live.format(t=f"{left // 60}:{left % 60:02d}") if left > 0 else over


_ADDON_LINES = {
    # (đã_chọn, chưa_chọn) — {p}=tiền tố "Người n/m: ", {n}=tên add-on đã chọn
    "vi": ("{p}Đã chọn: {n}. Bấm nút ✅ bên dưới để sang bước tiếp, hoặc chọn thêm / bỏ chọn add-on.",
           "{p}Anh/chị muốn thêm dịch vụ bổ sung nào không ạ? Chạm để chọn (được nhiều), hoặc bấm “Không thêm” để bỏ qua."),
    "en": ("{p}Selected: {n}. Tap the ✅ button below to continue, or add/remove more.",
           "{p}Any add-ons? Tap to select (you can pick several), or “No add-on” to skip."),
    "ja": ("{p}選択中：{n}。下の✅ボタンで次へ進むか、追加・解除できます。",
           "{p}追加オプションはいかがですか？タップで選択（複数可）、不要なら「追加なし」を押してください。"),
}


def _addon_prompt_line(session: Session, api_result: dict) -> str:
    """Câu hỏi add-on cho NGƯỜI hiện tại, có xác nhận lựa chọn (BR-10). Đây là câu tất định
    (ADDON ở _LITERAL_SAFE_KEYS) nên soạn trọn ở đây, khỏi lệ thuộc LLM."""
    s = session.slots
    lang = session.lang if session.lang in _ADDON_LINES else "vi"
    tong = s.party_size or 1
    idx = min(s.addon_guest_idx, max(tong, 1) - 1)
    sel_ids = s.guest_addons[idx] if idx < len(s.guest_addons) else []
    id2name = {a.get("id"): a.get("name") for a in (api_result or {}).get("addons", [])}
    names = ", ".join(id2name.get(i, "?") for i in sel_ids)
    sel_t, empty_t = _ADDON_LINES[lang]
    prefix = _addon_guest_prefix(session)
    return sel_t.format(p=prefix, n=names) if sel_ids else empty_t.format(p=prefix)


def _addon_guest_prefix(session: Session) -> str:
    """Tiền tố "Người n/m: " khi đặt NHÓM (add-on riêng từng người — BR-10). Đơn -> ''."""
    s = session.slots
    tong = s.party_size or 1
    if tong <= 1:
        return ""
    n = min(s.addon_guest_idx, tong - 1) + 1
    lang = session.lang
    if lang == "en":
        return f"Person {n}/{tong}: "
    if lang == "ja":
        return f"{n}/{tong}人目: "
    return f"Người {n}/{tong}: "


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
    total_addons = sum(len(g) for g in s.guest_addons)
    if total_addons:
        parts.append(f"+{total_addons} dịch vụ thêm")
    if s.therapist_gender:
        parts.append("nhân viên " + ("nữ" if s.therapist_gender == "female" else "nam"))
    elif s.therapist_id:
        parts.append("nhân viên đã chỉ định")
    # SĐT: chỉ nêu khi placeholder CÒN giải được (vault chưa rút) — nếu đã rút (vd sửa sau 2')
    # thì unmask ở cuối không thay được nữa, sẽ lộ "{{phone_1}}" ra câu. Bỏ qua cho sạch.
    if s.phone and not pii.unmask_value(s.phone, session.vault).startswith("{{"):
        parts.append(f"SĐT {s.phone}")   # placeholder -> unmask ở orchestrator
    return ", ".join(parts)
