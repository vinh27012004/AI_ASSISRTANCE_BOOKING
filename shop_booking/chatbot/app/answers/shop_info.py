"""Resolver hỏi thông tin cửa hàng — dùng lại NGUYÊN bộ API GĐ1, không đổi gì ở shop_api.

Mọi câu ở đây chứa SỐ LIỆU THẬT (giờ làm, địa chỉ, giá) nên được soạn TẤT ĐỊNH; NLG render
qua key INFO nằm trong _LITERAL_SAFE_KEYS, không cho LLM viết lại (§10 — cấm bịa số liệu).
"""

from __future__ import annotations

from datetime import date as _date, timedelta as _td

from app import matching, nlg
from app.answers.base import Answer, QueryCtx

_HORIZON_DAYS = 14          # khớp Orchestrator._AVAIL_HORIZON_DAYS
_WEEK_DAYS = 7


def _d(iso: str | None) -> str:
    """'2026-08-27' -> '27/8' (lối nói) — dùng lại nlg.format_date_list."""
    return nlg.format_date_list([iso]) if iso else ""


def _mins(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def _plus12(t: str) -> str | None:
    """'07:00' -> '19:00'. Dùng khi khách nói '7h' trần (không rõ sáng hay tối)."""
    try:
        m = _mins(t) + 12 * 60
    except (ValueError, IndexError):
        return None
    return f"{(m // 60) % 24:02d}:{m % 60:02d}" if m < 24 * 60 else None


def _today() -> str:
    return _date.today().isoformat()


def _resolve_shop(ctx: QueryCtx, shops: list[dict]) -> dict | None:
    """Cửa hàng khách đang hỏi: tên NLU trích -> tên nằm trong câu gốc -> shop đang chọn
    trong phiên -> chịu (hỏi lại).

    Lùi về câu gốc vì nhánh rule-based (chưa cấu hình LLM) không biết tên cửa hàng — chúng
    đến từ API. pick_unique khớp chuỗi-con hai chiều nên "Cửa hàng Sendai ở đâu?" trúng
    đúng một shop, còn "địa chỉ các cửa hàng?" không trúng shop nào -> liệt kê tất cả."""
    q = ctx.entities.get("shop")
    sh = matching.pick_unique(str(q), shops) if q else None
    if sh is None and ctx.raw_text:
        sh = matching.pick_unique(ctx.raw_text, shops)
    if sh is None and ctx.shop_id:
        sh = next((s for s in shops if s.get("id") == ctx.shop_id), None)
    return sh


_ASK_WHICH_SHOP = "Anh/chị muốn hỏi cửa hàng nào ạ?"


# --------------------------------------------------------------------------- #
#  "Cửa hàng nào còn mở lúc 7h?"                                               #
# --------------------------------------------------------------------------- #

def _has_shift_at(api, shop_id: int, day: str, t: str) -> bool:
    """Bảng shop KHÔNG có open_time/close_time (models/shop.py) — trong hệ này "mở cửa" =
    CÓ NHÂN VIÊN CÓ CA, đúng định nghĩa mà GET /availability dùng. GET /timeline trả
    therapists[].shifts[] nên xét: tồn tại ca thỏa start <= t < end."""
    data = api.get_timeline(shop_id, day)
    try:
        m = _mins(t)
    except (ValueError, IndexError):
        return False
    for th in data.get("therapists", []):
        for sh in th.get("shifts", []):
            try:
                if _mins(sh["start_time"]) <= m < _mins(sh["end_time"]):
                    return True
            except (ValueError, KeyError, IndexError):
                continue
    return False


def _shifts(api, shop_id: int, day: str) -> list[dict]:
    data = api.get_timeline(shop_id, day)
    return [sh for th in data.get("therapists", []) for sh in th.get("shifts", [])]


def _day_hours(api, shops: list[dict], day: str) -> tuple[str, str] | None:
    """Khung giờ làm của cả hệ thống trong ngày: (sớm nhất mở, muộn nhất đóng).

    Dùng khi giờ khách hỏi không ai mở — nói khung giờ hữu ích hơn hẳn "không có" rồi để
    khách tự mò, và trả lời đúng cho cả giờ QUÁ SỚM lẫn QUÁ MUỘN (nói giờ đóng muộn nhất
    thì khách hỏi 5h sáng đọc thấy lạc đề)."""
    lo, hi = None, None
    for s in shops:
        for sh in _shifts(api, s["id"], day):
            try:
                st, en = _mins(sh["start_time"]), _mins(sh["end_time"])
            except (ValueError, KeyError, IndexError):
                continue
            lo = st if lo is None else min(lo, st)
            hi = en if hi is None else max(hi, en)
    if lo is None or hi is None:
        return None
    return f"{lo // 60:02d}:{lo % 60:02d}", f"{hi // 60:02d}:{hi % 60:02d}"


def shops_open_at(ctx: QueryCtx, api) -> Answer:
    t = ctx.entities.get("time")
    if not t:
        return Answer("Anh/chị muốn hỏi cửa hàng mở vào khung giờ nào ạ?")
    day = ctx.entities.get("date") or ctx.date or _today()
    times = [t]
    if ctx.time_ambiguous:                      # "7h" trần -> trả lời cả 07:00 lẫn 19:00
        alt = _plus12(t)
        if alt:
            times.append(alt)

    shops = api.get_shops()
    parts, found = [], False
    for tt in times:
        names = [s["name"] for s in shops if _has_shift_at(api, s["id"], day, tt)]
        if names:
            found = True
        parts.append(f"lúc {tt} có {', '.join(names)}" if names
                     else f"lúc {tt} thì không cửa hàng nào làm")
    # Ngày phải nói RÕ: ca làm đổi theo từng ngày, không nói thì khách hiểu thành "luôn mở".
    head = f"Ngày {_d(day)}, {'; '.join(parts)} ạ."

    if found:
        # Chỉ nhắc "giờ trống xem sau" khi THỰC SỰ có cửa hàng — không có mà vẫn nói thì
        # câu vừa thừa vừa khó hiểu (khách phản ánh).
        return Answer(head + " Đây là giờ cửa hàng có nhân viên làm; giờ trống cụ thể em "
                             "xem giúp sau khi anh/chị chọn gói dịch vụ ạ.")

    hours = _day_hours(api, shops, day)
    if hours:                                   # đừng để câu trả lời cụt ở chỗ "không có"
        return Answer(f"{head} Hôm đó cửa hàng làm từ {hours[0]} đến {hours[1]}, "
                      "anh/chị chọn giúp em giờ trong khoảng này nhé.")
    return Answer(f"{head} Anh/chị thử hỏi giúp em ngày khác nhé.")


# --------------------------------------------------------------------------- #
#  "Shop A ở đâu?" / "số điện thoại cửa hàng?"                                 #
# --------------------------------------------------------------------------- #

def shop_contact(ctx: QueryCtx, api) -> Answer:
    shops = api.get_shops()
    sh = _resolve_shop(ctx, shops)
    if sh:
        # KHÔNG suggest: hỏi "Shop A ở đâu?" không có nghĩa là khách muốn đặt ở đó.
        return Answer(f"{sh['name']} ở {sh['address']}, số điện thoại {sh['phone']} ạ.")
    items = "; ".join(f"{s['name']} — {s['address']} — {s['phone']}" for s in shops)
    return Answer(f"Dạ thông tin các cửa hàng ạ: {items}.")


# --------------------------------------------------------------------------- #
#  "Chủ nhật có làm không?" / "cửa hàng nghỉ ngày nào?"                        #
# --------------------------------------------------------------------------- #

def shop_days_off(ctx: QueryCtx, api) -> Answer:
    shops = api.get_shops()
    sh = _resolve_shop(ctx, shops)
    if sh is None:
        return Answer(_ASK_WHICH_SHOP)

    today = _date.today()
    to = today + _td(days=_HORIZON_DAYS - 1)
    data = api.get_availability(sh["id"], today.isoformat(), to.isoformat())
    open_dates = data.get("open_dates") or []
    closed_dates = data.get("closed_dates") or []

    d = ctx.entities.get("date")
    if d:
        if d in open_dates:
            return Answer(f"Dạ ngày {_d(d)} {sh['name']} có làm ạ.")
        if d in closed_dates:
            return Answer(f"Dạ ngày {_d(d)} {sh['name']} không có lịch làm ạ. "
                          f"Các ngày còn nhận: {nlg.format_date_list(open_dates)}.")
        return Answer(f"Dạ ngày {_d(d)} ngoài lịch em xem được (em chỉ nắm "
                      f"{_HORIZON_DAYS} ngày tới) ạ.")

    horizon = (today + _td(days=_WEEK_DAYS)).isoformat()
    off = [x for x in closed_dates if x <= horizon]
    if off:
        return Answer(f"Trong 7 ngày tới {sh['name']} nghỉ các ngày: "
                      f"{nlg.format_date_list(off)} ạ.")
    return Answer(f"Dạ 7 ngày tới {sh['name']} làm tất cả các ngày ạ.")


# --------------------------------------------------------------------------- #
#  "Gói toàn thân bao nhiêu tiền?"                                             #
# --------------------------------------------------------------------------- #

def course_price(ctx: QueryCtx, api) -> Answer:
    shops = api.get_shops()
    sh = _resolve_shop(ctx, shops)
    if sh is None:
        return Answer(_ASK_WHICH_SHOP)

    day = ctx.entities.get("date") or ctx.date or _today()
    data = api.get_services(sh["id"], day, ctx.party_size)
    courses = data.get("courses") or []
    if not courses:                             # A1: ngày shop nghỉ -> 200 rỗng
        return Answer(f"Dạ ngày {_d(day)} {sh['name']} không phục vụ nên em chưa xem được "
                      "bảng giá ạ. Anh/chị hỏi giúp em ngày khác nhé.")
    # Cùng định dạng với danh sách gói ở nlg._facts_for cho nhất quán.
    items = ", ".join(f"{c.get('name')} · {c.get('duration_min')} phút · {c.get('price')}¥"
                      for c in courses)
    return Answer(f"Dạ bảng giá {sh['name']} ạ: {items}.")
