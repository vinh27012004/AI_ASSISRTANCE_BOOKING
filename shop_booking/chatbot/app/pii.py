"""PII Masking — chatbot-architecture.md §6, DD §2.5/§3.4, Q6.

LLM gọi qua router bên ngoài ⇒ SĐT/email/mã đặt chỗ KHÔNG bao giờ rời hệ thống mình.
Cơ chế: bắt PII bằng regex trước khi ra LLM → thay bằng placeholder → giữ giá trị thật
trong Vault (phía mình) → thay lại giá trị thật chỉ khi (a) gọi shop_api, (b) ghép câu
cuối trả cho widget của CHÍNH khách.

Nguyên tắc Q6: "mask thừa hơn sót" — regex phủ rộng (VN + JP, nhiều separator). Tên khách
KHÔNG dựa regex: strip cứng ở mask_response() trước khi bất kỳ field nào vào context LLM.
"""

from __future__ import annotations

import re
from typing import Any

Vault = dict[str, str]  # placeholder -> giá trị thật

# Mã đặt chỗ: {yyyyMMdd}-{shop_code}-{rand} (api-design 1.5). Đặt TRƯỚC phone để 8 số
# đầu của mã không bị bắt nhầm thành số điện thoại.
_CODE_RE = re.compile(r"\b\d{8}-[A-Za-z0-9]+-[A-Za-z0-9]+\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
# Chuỗi trông như SĐT: cho phép +, khoảng trắng, '-', '.'; lọc lại theo số chữ số (9–15)
# để phủ VN (10 số, +84), JP (0x0-xxxx-xxxx, +81, free-dial 0120). Bắt rộng có chủ đích.
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w])\+?\d[\d\-.\s]{7,}\d(?![\w])")


def _next_placeholder(vault: Vault, kind: str) -> str:
    n = 1 + sum(1 for k in vault if k.startswith("{{" + kind + "_"))
    return f"{{{{{kind}_{n}}}}}"


def _intern(vault: Vault, kind: str, value: str) -> str:
    """Trả placeholder cho `value`; tái dùng nếu value đã có trong vault (không đẻ trùng)."""
    for ph, real in vault.items():
        if real == value and ph.startswith("{{" + kind + "_"):
            return ph
    ph = _next_placeholder(vault, kind)
    vault[ph] = value
    return ph


def mask(text: str, vault: Vault, *, extra_values: list[str] | None = None) -> str:
    """Che PII trong `text`, nạp giá trị thật vào `vault`, trả text đã che.

    `extra_values`: giá trị cần che ĐÍCH DANH ngoài regex — dùng cho mã đặt chỗ đã biết
    trong phiên (Q6: mask bằng CẢ regex LẪN giá trị thật trong vault)."""
    if not text:
        return text

    # 1) Giá trị đích danh đã biết (vd booking_code hiện tại) — khớp nguyên văn.
    for val in extra_values or []:
        if val and val in text:
            text = text.replace(val, _intern(vault, "code", val))

    # 2) Mã đặt chỗ theo regex.
    text = _CODE_RE.sub(lambda m: _intern(vault, "code", m.group(0)), text)
    # 3) Email.
    text = _EMAIL_RE.sub(lambda m: _intern(vault, "email", m.group(0)), text)

    # 4) Điện thoại — lọc ứng viên theo số chữ số để tránh bắt năm/số lẻ.
    def _phone_sub(m: re.Match) -> str:
        raw = m.group(0)
        digits = re.sub(r"\D", "", raw)
        if not (9 <= len(digits) <= 15):
            return raw
        return _intern(vault, "phone", raw.strip())

    text = _PHONE_CANDIDATE_RE.sub(_phone_sub, text)
    return text


def unmask(text: str, vault: Vault) -> str:
    """Thay placeholder bằng giá trị thật — CHỈ cho câu bot trả widget của chính khách,
    hoặc khi build request gọi shop_api."""
    if not text:
        return text
    for ph, real in vault.items():
        text = text.replace(ph, real)
    return text


def unmask_value(value: str | None, vault: Vault) -> str | None:
    """Unmask một field lẻ (vd slots.phone = '{{phone_1}}') khi build BookingCreateRequest."""
    if value is None:
        return None
    return vault.get(value, value)


# Field mang tên khách — strip cứng, không đưa vào context LLM (Q6). shop_api hiện không
# trả tên khách; danh sách để phòng thủ khi response mở rộng sau này.
_CUSTOMER_PII_KEYS = {"customer_name", "name", "phone", "email"}


def mask_response(obj: Any) -> Any:
    """Làm sạch response API TRƯỚC khi bất kỳ field nào lọt vào prompt LLM.

    - Xóa `customer_name` ở mọi cấp.
    - Trong object khóa `customer`, xóa name/phone/email (chỉ giữ member_type/rank/visit_count).
    Tên shop/course/therapist/addon (khóa `name` ở NGOÀI `customer`) được GIỮ — là dữ liệu
    nghiệp vụ, không phải PII khách."""
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k == "customer_name":
                continue
            if k == "customer" and isinstance(v, dict):
                out[k] = {ck: cv for ck, cv in v.items() if ck not in _CUSTOMER_PII_KEYS}
                continue
            out[k] = mask_response(v)
        return out
    if isinstance(obj, list):
        return [mask_response(x) for x in obj]
    return obj
