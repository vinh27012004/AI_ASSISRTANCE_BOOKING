"""NLG (bước ⑤⑥) — DD §3.1, chatbot-architecture.md §1/§10. Chỉ phục vụ TIẾNG VIỆT.

⑤ build_prompt: code ghép instruction[state] + facts (facts lấy TỪ slots/api_result —
   mọi số liệu từ đây, LLM không được bịa §10).
⑥ generate: LLM diễn đạt tự nhiên; không có router thì dùng câu mẫu offline (templates.FAKE).

Câu bot vẫn ở dạng ĐÃ MASK (chứa {{phone_1}}…); orchestrator unmask ở cuối trước khi trả
widget của chính khách.
"""

from __future__ import annotations

import json
import logging
import time

from app import pii, templates
from app.llm_client import LLMError, RealLLMClient
from app.session import Session

logger = logging.getLogger(__name__)

_NLG_SYSTEM = (
    "Bạn là trợ lý đặt lịch massage, nói chuyện lịch sự, ngắn gọn, LUÔN bằng TIẾNG VIỆT. "
    "CHỈ dùng dữ kiện trong 'facts'; TUYỆT ĐỐI KHÔNG bịa giá, dịch vụ hay "
    "khung giờ không có.\n"
    "Khách KHÔNG có nút để bấm, chỉ trả lời bằng lời — nên khi 'facts' có danh sách lựa chọn "
    "(cửa hàng, gói dịch vụ, giờ trống, nhân viên...) thì PHẢI ĐỌC RÕ các lựa chọn đó trong "
    "câu, ĐẦY ĐỦ và ĐÚNG NGUYÊN VĂN, để khách biết mà chọn.\n"
    "TUYỆT ĐỐI KHÔNG tự tạo chỗ trống dạng {{...}} (không viết {{courses}}, {{slots}}...). "
    "Chỉ khi trong 'facts' CÓ SẴN placeholder PII như {{phone_1}} thì giữ NGUYÊN VĂN nó. "
    "Trả về VĂN BẢN THUẦN — TUYỆT ĐỐI KHÔNG markdown (không **in đậm**, không gạch đầu "
    "dòng, không tiêu đề): câu được hiển thị/đọc NGUYÊN VĂN trên widget và điện thoại. "
    "Trả về DUY NHẤT câu trả lời cho khách, không kèm giải thích."
)


def build_prompt(state_key: str, session: Session, api_result: dict) -> dict:
    """Trả prompt có cấu trúc cho bước ⑥. `state_key` có thể là state hoặc nhánh đặc biệt
    (REPROMPT/HANDOFF/END/ERROR)."""
    facts = _facts_for(state_key, session, api_result)
    return {
        "key": state_key,
        "instruction": templates.INSTRUCTION.get(state_key, templates.INSTRUCTION["REPROMPT"]),
        "facts": facts,
    }


# Câu chứa số/mã THẬT mà LLM có thể sửa/bịa (shop_phone, booking_code, giờ trống) -> LUÔN
# dùng template code, KHÔNG qua LLM (§10: cấm LLM tự sinh số liệu).
# ADDON: khi đặt NHÓM, câu phải nêu ĐANG hỏi "Người n/m" (BR-10) — để LLM diễn đạt thì nó
# hay bỏ mất chỉ số này, khiến người 2 nhìn giống hỏi lại người 1. Ép template tất định.
# MODIFY: menu "đổi gì" — ép template tất định để chèn đồng hồ "sửa nhanh còn ~m:ss" (BR-17)
# chính xác, không để LLM bịa/bỏ.
_LITERAL_SAFE_KEYS = {"SLOT", "ADDON", "MODIFY", "END", "HANDOFF", "ERROR", "DONE", "UPDATED", "CANCELLED"}


def generate(prompt: dict, llm: RealLLMClient | None) -> str:
    """Sinh câu. Không router HOẶC câu chứa số/mã thật -> câu mẫu code (khớp chính xác)."""
    if llm is None or prompt["key"] in _LITERAL_SAFE_KEYS:
        return templates.fake_sentence(prompt["key"], prompt["facts"])
    try:
        user = json.dumps(
            {k: prompt[k] for k in ("instruction", "facts")},
            ensure_ascii=False,
        )
        text = llm.complete(_NLG_SYSTEM, user, temperature=0.4, max_tokens=400)
        if text.strip():
            return text.strip()
        # LLM trả rỗng — khách vẫn thấy câu bình thường (câu mẫu) nên lỗi này dễ lọt qua
        # nếu không log riêng ở đây (llm_client.py chỉ thấy "gọi thành công", không biết
        # nội dung rỗng có phải là fallback hay không).
        logger.warning("nlg: LLM trả rỗng cho key=%s -> dùng câu mẫu", prompt["key"])
        return templates.fake_sentence(prompt["key"], prompt["facts"])
    except LLMError as e:
        # Khách vẫn nhận được câu trả lời bình thường (câu mẫu offline) nên /chat/message
        # vẫn 200 — muốn biết lượt này đã fallback vì LLM lỗi thì PHẢI có dòng log này (lỗi
        # kết nối/HTTP đã log ở app.llm_client, nhưng đó chỉ là log của lời gọi, không nói
        # rõ hậu quả: câu trả lời thực tế khách nhận được có phải hàng thật hay không).
        logger.warning("nlg: LLM lỗi (%s) cho key=%s -> dùng câu mẫu", e, prompt["key"])
        return templates.fake_sentence(prompt["key"], prompt["facts"])


# --------------------------------------------------------------------------- #
#  Facts                                                                       #
# --------------------------------------------------------------------------- #

def _facts_for(state_key: str, session: Session, api_result: dict) -> dict:
    ar = api_result or {}
    facts: dict = {}

    # Đưa lựa chọn THẬT vào facts (LLM có dữ liệu chính xác, khỏi bịa placeholder/giá).
    # Không còn nút bấm -> kèm luôn bản đã nối chuỗi (*_list) để câu mẫu offline đọc thẳng ra.
    if state_key == "SHOP":
        names = [sh.get("name") for sh in ar.get("shops", [])]
        facts["cua_hang"] = names
        facts["cua_hang_list"] = ", ".join(n for n in names if n)
    elif state_key == "DATE":
        facts["ngay_list"] = _date_list_line(ar.get("active_dates"))
    elif state_key == "COURSE":
        courses = [
            f'{c.get("name")} · {c.get("duration_min")} phút · {c.get("price")}¥'
            for c in ar.get("courses", [])
        ]
        facts["course"] = courses
        facts["course_list"] = ", ".join(courses)
    elif state_key == "ADDON":
        # Đọc danh sách add-on cho ĐÚNG người đang hỏi (BR-10) — soạn tất định ở dưới.
        facts["addon_line"] = _addon_prompt_line(session, ar)
    elif state_key == "THERAPIST":
        people = [
            f'{t.get("name")} ({"nữ" if t.get("gender") == "female" else "nam"})'
            for t in ar.get("therapists", [])
        ]
        facts["nhan_vien"] = people
        # Có người trực thì đọc tên ra; không có thì để rỗng (câu vẫn mời chọn theo giới tính).
        facts["nhan_vien_list"] = (
            f'Hôm đó có {", ".join(people)}. ' if people else ""
        )
    elif state_key == "CONTACT":
        facts["hoi"] = _contact_ask(session)      # CHỈ những gì còn thiếu (phone/email)

    if state_key == "SLOT":
        times = ar.get("slots") or ar.get("suggested_slots") or []
        facts["gio_trong"] = times
        facts["slots"] = ", ".join(times) if times else "(chưa có)"
        # Khách đã nêu một giờ nhưng giờ đó hết chỗ -> nói thẳng ra, đừng lặng lẽ đọc danh
        # sách khác (khách sẽ tưởng bot bỏ qua lời mình).
        het = ar.get("wanted_time_unavailable")
        facts["gio_het"] = f"Giờ {het} không còn trống ạ. " if het else ""
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
_QUICK_EDIT = (" ⏱ Sửa/hủy nhanh (không cần nhập email) còn khoảng {t}.",
               " ⏱ Cửa sổ sửa nhanh 2 phút đã hết — sửa/hủy sẽ cần nhập lại email.")


# Danh sách ngày cửa hàng còn làm — đọc ra để khách biết mà chọn.
# Dài quá thì cắt bớt, khách vẫn nói được ngày khác và orchestrator hiểu ("31", "31/7"…).
_DATE_LIST_LIMIT = 8
_DATE_LIST_LINE = "Cửa hàng còn nhận các ngày: {d}."


def format_date_list(dates, limit: int = _DATE_LIST_LIMIT) -> str:
    """'2026-07-31' -> '31/7' — đọc theo lối nói (bỏ số 0 thừa), nối bằng dấu phẩy."""
    from datetime import date as _date

    shown = []
    for iso in (dates or [])[:limit]:
        try:
            d = _date.fromisoformat(iso)
            shown.append(f"{d.day}/{d.month}")
        except ValueError:
            shown.append(iso)
    return ", ".join(shown)


def _date_list_line(active_dates) -> str:
    """Câu đọc các ngày còn mở. Chưa dò được (API lỗi) -> rỗng, câu chỉ hỏi chung chung."""
    if not active_dates:
        return ""
    return _DATE_LIST_LINE.format(d=format_date_list(active_dates))


def _quick_edit_note(session: Session) -> str:
    """Nhắc cửa sổ sửa nhanh 2' (BR-17): còn giờ -> 'còn ~m:ss'; hết -> nhắc cần email.
    Chatbot không tick liên tục được nên đây là ẢNH CHỤP lúc gửi tin (cập nhật mỗi lượt)."""
    exp = session.edit_token_expires_at
    if not exp:
        return ""
    left = int(round(exp - time.time()))
    live, over = _QUICK_EDIT
    return live.format(t=f"{left // 60}:{left % 60:02d}") if left > 0 else over


# {p}=tiền tố "Người n/m: ", {ds}=danh sách add-on đọc ra cho khách chọn
_ADDON_LINE = ("{p}Anh/chị muốn thêm dịch vụ bổ sung nào không ạ? Hiện có: {ds}. "
               "Anh/chị đọc tên dịch vụ muốn thêm, hoặc nói “không” để bỏ qua.")


def _addon_prompt_line(session: Session, api_result: dict) -> str:
    """Câu hỏi add-on cho NGƯỜI hiện tại (BR-10). Đây là câu tất định (ADDON ở
    _LITERAL_SAFE_KEYS) nên soạn trọn ở đây, khỏi lệ thuộc LLM.

    Không còn nút -> phải ĐỌC danh sách add-on ra. Add-on bị cấm với course đang chọn
    (BR-09) bị loại khỏi danh sách để không mời nhầm (A3 sớm)."""
    s = session.slots
    offered = [
        f'{a.get("name")} · {a.get("duration_min")} phút'
        for a in (api_result or {}).get("addons", [])
        if not (s.course_id and s.course_id in (a.get("restricted_course_ids") or []))
    ]
    return _ADDON_LINE.format(p=_addon_guest_prefix(session), ds=", ".join(offered))


def _addon_guest_prefix(session: Session) -> str:
    """Tiền tố "Người n/m: " khi đặt NHÓM (add-on riêng từng người — BR-10). Đơn -> ''."""
    s = session.slots
    tong = s.party_size or 1
    if tong <= 1:
        return ""
    n = min(s.addon_guest_idx, tong - 1) + 1
    return f"Người {n}/{tong}: "


def _contact_ask(session: Session) -> str:
    """Chuỗi thông tin CÒN THIẾU ở CONTACT — đã có số thì chỉ hỏi email, và ngược lại."""
    s = session.slots
    missing = []
    if not s.phone:
        missing.append("số điện thoại")
    if not s.email:
        missing.append("email")
    if not missing:                              # cả hai đã có -> nêu chung (hiếm khi tới đây)
        missing = ["số điện thoại", "email"]
    return " và ".join(missing)


def _order_summary(session: Session, api_result: dict) -> str:
    """Đọc lại đơn ở CONFIRM. Dùng tên course/giờ; SĐT/email để placeholder (unmask ở cuối)."""
    s = session.slots
    parts = []
    # Tên CỬA HÀNG phải có: đây là thứ khách chọn đầu tiên và cũng là thứ dễ nhầm nhất khi
    # đặt nhiều chi nhánh — thiếu nó khách không xác nhận được đơn có đúng chỗ mình muốn.
    if s.shop_name:
        parts.append(s.shop_name)
    if s.date:
        parts.append(f"ngày {format_date_list([s.date])}")   # 5/8 thay vì 2026-08-05
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
