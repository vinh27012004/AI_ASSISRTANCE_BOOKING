"""Khớp khu vực: "cửa hàng nào gần nhà tôi?" — khách nêu địa chỉ, bot chỉ ra shop CÙNG KHU VỰC.

Bảng shop KHÔNG có toạ độ (models/shop.py chỉ có address dạng chữ) nên KHÔNG tính được
khoảng cách. Ở đây chỉ khớp token hành chính, và câu trả lời cũng chỉ nói "cùng khu vực" —
không hứa "gần nhất", vì dữ liệu không đỡ được lời hứa đó.

Địa chỉ khách là PII: chỉ so trong bộ nhớ, KHÔNG đưa vào params/body của bất kỳ lời gọi
shop_api nào (shop_api_client log cả params).
"""

from __future__ import annotations

import re

from app.answers.base import Answer, QueryCtx

# Từ không mang thông tin khu vực: hư từ tiếng Việt, chữ trong tên cửa hàng, và hậu tố
# hành chính Nhật (shi/ku/cho...) — giữ lại thì shop nào cũng trúng.
_STOPWORDS = {
    "cửa", "hàng", "cua", "hang", "shop", "quán", "quan", "chi", "nhánh", "nhanh",
    "nhà", "nha", "tôi", "toi", "em", "anh", "chị", "chi̇", "mình", "minh", "ạ", "a",
    "ở", "o", "đâu", "dau", "nào", "nao", "gần", "gan", "nhất", "nhat", "quanh", "đây",
    "day", "có", "co", "không", "khong", "vậy", "vay", "thế", "the", "cho", "biết",
    "biet", "muốn", "muon", "đặt", "dat", "lịch", "lich", "massage", "địa", "dia",
    "chỉ", "của", "cua̒", "là", "la", "với", "voi", "và", "va", "thành", "thanh",
    "phố", "pho", "tỉnh", "tinh", "quận", "quan̈", "phường", "phuong", "số", "so",
    "shi", "ku", "cho", "ken", "fu", "to", "machi", "chome",
}

_TOKEN_RE = re.compile(r"[0-9a-zà-ỹ]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """Tách token khu vực: bỏ mã bưu điện 〒xxx-xxxx, tách chữ/số, bỏ hư từ và token
    quá ngắn (≤2 ký tự trúng bừa quá nhiều)."""
    low = re.sub(r"〒\s*\d{3}-?\d{4}", " ", (text or "").lower())
    out = set()
    for tk in _TOKEN_RE.findall(low):
        if len(tk) > 2 and not tk.isdigit() and tk not in _STOPWORDS:
            out.add(tk)
    return out


def _shop_line(s: dict) -> str:
    return f"{s['name']} ({s['address']})"


def shops_near(ctx: QueryCtx, api) -> Answer:
    # NLU trích được `location` thì dùng; nhánh rule-based không biết tên địa danh nên lùi
    # về cả câu — bộ stopword ở trên đã lọc phần lớn nhiễu.
    query = ctx.entities.get("location") or ctx.raw_text
    want = _tokens(str(query or ""))
    if not want:
        return Answer("Anh/chị cho em biết khu vực (thành phố/quận) giúp em với ạ?")

    shops = api.get_shops()
    scored = [(len(want & _tokens(f"{s.get('name')} {s.get('address')}")), s) for s in shops]
    best = max((n for n, _ in scored), default=0)
    hits = [s for n, s in scored if n == best and n > 0]

    if len(hits) == 1:
        s = hits[0]
        # Khớp DUY NHẤT -> vừa trả lời vừa đề xuất điền ô cửa hàng. Đây là "phiếu đề xuất":
        # orchestrator đưa qua sm.merge_params, resolver không tự ghi vào tờ đơn.
        return Answer(f"Dạ cùng khu vực với anh/chị có {_shop_line(s)} ạ.",
                      suggest={"shop": s.get("name")})
    if len(hits) > 1:                      # mơ hồ -> đọc danh sách, KHÔNG chọn thay khách
        items = ", ".join(_shop_line(s) for s in hits)
        return Answer(f"Dạ cùng khu vực có mấy cửa hàng ạ: {items}. Anh/chị chọn giúp em một nơi nhé.")

    items = ", ".join(_shop_line(s) for s in shops)
    return Answer("Dạ em chưa thấy cửa hàng nào ngay khu vực đó. "
                  f"Hiện bên em có: {items}.")
