"""Resolver hỏi thông tin cửa hàng — dùng lại NGUYÊN bộ API GĐ1, không đổi gì ở shop_api.

Mọi câu ở đây chứa SỐ LIỆU THẬT (giờ làm, địa chỉ, giá) nên được soạn TẤT ĐỊNH; NLG render
qua key INFO nằm trong _LITERAL_SAFE_KEYS, không cho LLM viết lại (§10 — cấm bịa số liệu).
"""

from __future__ import annotations

import re
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

# Câu hỏi NỐI TIẾP câu trước ("trong 2 cửa hàng đó cái nào…"). Cố ý là CỤM, không phải chữ
# "đó" trần — "ngày đó", "giờ đó" nhan nhản, bắt bừa là lọc nhầm.
_REFER_BACK = ("cửa hàng đó", "cua hang do", "cửa hàng trên", "trong đó", "trong số đó",
               "trong 2 cửa hàng", "trong hai cửa hàng", "trong 3 cửa hàng",
               "mấy cửa hàng đó", "những cửa hàng đó", "các cửa hàng đó",
               "vừa rồi", "vừa nêu", "kể trên", "nói trên")


def _scope(ctx: QueryCtx, shops: list[dict]) -> list[dict]:
    """Thu hẹp về danh sách câu trước NẾU khách đang hỏi nối tiếp. Không thì giữ nguyên."""
    low = (ctx.raw_text or "").lower()
    if not ctx.shortlist or not any(w in low for w in _REFER_BACK):
        return shops
    narrowed = [s for s in shops if s.get("id") in ctx.shortlist]
    return narrowed or shops


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


def _shops_open_on(api, day: str) -> Answer:
    """Cửa hàng CÓ LÀM trong ngày (có ít nhất một ca) — không xét giờ cụ thể."""
    hits = [s for s in api.get_shops() if _shifts(api, s["id"], day)]
    if not hits:
        return Answer(f"Dạ ngày {_d(day)} không cửa hàng nào có lịch làm ạ. "
                      "Anh/chị thử hỏi giúp em ngày khác nhé.")
    return Answer(f"Ngày {_d(day)} các cửa hàng có làm là: "
                  f"{', '.join(s['name'] for s in hits)} ạ.",
                  shortlist=tuple(s["id"] for s in hits))


def shops_list(ctx: QueryCtx, api) -> Answer:
    """"Bên mình có những cửa hàng nào?" — câu cơ bản nhất, trước đây rơi vào nhánh
    "em chưa hỗ trợ được" vì NLU gán question_type=other."""
    shops = api.get_shops()
    items = "; ".join(f"{s['name']} ({s['address']})" for s in shops)
    return Answer(f"Dạ bên em có {len(shops)} cửa hàng ạ: {items}.",
                  shortlist=tuple(s["id"] for s in shops))


# --------------------------------------------------------------------------- #
#  "Cửa hàng nào đang có 3 nữ phục vụ?"                                        #
# --------------------------------------------------------------------------- #

_GENDER_VI = {"female": "nữ", "male": "nam"}


def _wanted_gender(value) -> str | None:
    v = str(value or "").strip().lower()
    if v in ("female", "nu", "nữ"):
        return "female"
    if v in ("male", "nam"):
        return "male"
    return None                          # tên riêng / không nêu -> đếm mọi nhân viên


def _staff_count(api, shop_id: int, day: str, gender: str | None) -> int:
    data = api.get_timeline(shop_id, day)
    return len([t for t in data.get("therapists", [])
                if t.get("shifts") and (gender is None or t.get("gender") == gender)])


# "3 nữ", "2 nhân viên", "3 người" — NLU stateless hay bỏ sót vì thiếu chữ "người".
_COUNT_RE = re.compile(r"(\d{1,2})\s*(?:nữ|nu|nam|nhân viên|nhan vien|người|nguoi)")


def _wanted_count(ctx: QueryCtx) -> tuple[int, bool]:
    """(Số nhân viên cần, khách CÓ nêu số hay không).

    Cờ thứ hai để khỏi viết "đủ 1 nhân viên nữ" khi khách chỉ hỏi "cửa hàng nào có nhân
    viên nữ" — câu đó đọc rất kỳ."""
    m = _COUNT_RE.search((ctx.raw_text or "").lower())
    if m:
        return max(int(m.group(1)), 1), True
    ps = ctx.entities.get("party_size") or ctx.party_size
    try:
        return (max(int(ps), 1), True) if ps else (1, False)
    except (TypeError, ValueError):
        return 1, False


def shops_by_staff(ctx: QueryCtx, api) -> Answer:
    """Lọc cửa hàng theo SỐ nhân viên trực (và giới tính nếu khách nêu).

    Đếm người CÓ CA trong ngày qua /timeline — điều kiện cần, chưa xét họ còn trống giờ
    nào, nên câu trả lời chỉ nói "có ... trực", không hứa đặt được."""
    day = ctx.entities.get("date") or ctx.date or _today()
    gender = _wanted_gender(ctx.entities.get("therapist"))
    need, said_count = _wanted_count(ctx)

    gv = f" {_GENDER_VI[gender]}" if gender else ""
    nhan = f"{need} nhân viên{gv}" if said_count else f"nhân viên{gv}"
    rows = [(s, _staff_count(api, s["id"], day, gender)) for s in _scope(ctx, api.get_shops())]
    ok = [s for s, n in rows if n >= need]

    # Nhóm ≥2 không được chỉ định người (BR-04) -> nói trước, khỏi hụt hẫng ở bước sau.
    luu_y = (" Lưu ý nhóm từ 2 người thì cửa hàng sắp nhân viên giúp, mình không chỉ định "
             "được ạ." if need >= 2 and gender else "")

    if not ok:
        best_s, best_n = max(rows, key=lambda r: r[1], default=(None, 0))
        if best_s is None or best_n == 0:
            return Answer(f"Dạ ngày {_d(day)} chưa cửa hàng nào có {nhan} trực ạ. "
                          "Anh/chị thử hỏi giúp em ngày khác nhé.")
        return Answer(f"Dạ ngày {_d(day)} chưa cửa hàng nào đủ {nhan} trực ạ. Nhiều nhất là "
                      f"{best_s['name']} với {best_n} người.{luu_y}")

    names = ", ".join(s["name"] for s in ok)
    du = "đủ " if said_count else ""
    ids = tuple(s["id"] for s in ok)
    text = f"Ngày {_d(day)}, cửa hàng có {du}{nhan} trực: {names} ạ.{luu_y}"
    # Duy nhất -> đề xuất điền luôn ô cửa hàng.
    return Answer(text, shortlist=ids,
                  suggest={"shop": ok[0]["name"]} if len(ok) == 1 else {})


def shops_open_at(ctx: QueryCtx, api) -> Answer:
    t = ctx.entities.get("time")
    day = ctx.entities.get("date") or ctx.date or _today()
    if not t:
        # "Cửa hàng nào đang mở hôm nay?" — hỏi theo NGÀY chứ không theo giờ. Hỏi ngược
        # "khung giờ nào ạ?" là né câu hỏi (khách phản ánh); trả lời thẳng theo ngày.
        return _shops_open_on(api, day)
    times = [t]
    if ctx.time_ambiguous:                      # "7h" trần -> trả lời cả 07:00 lẫn 19:00
        alt = _plus12(t)
        if alt:
            times.append(alt)

    shops = _scope(ctx, api.get_shops())
    parts, found, hit_ids = [], False, []
    for tt in times:
        matched = [s for s in shops if _has_shift_at(api, s["id"], day, tt)]
        names = [s["name"] for s in matched]
        if names:
            found = True
            hit_ids += [s["id"] for s in matched if s["id"] not in hit_ids]
        parts.append(f"lúc {tt} có {', '.join(names)}" if names
                     else f"lúc {tt} thì không cửa hàng nào làm")
    # Ngày phải nói RÕ: ca làm đổi theo từng ngày, không nói thì khách hiểu thành "luôn mở".
    head = f"Ngày {_d(day)}, {'; '.join(parts)} ạ."

    if found:
        # Trả lời ĐÚNG câu hỏi: có cửa hàng nào. Không kèm giải thích về giờ trống của nhân
        # viên — khách chỉ hỏi cửa hàng, thêm vào chỉ làm câu dài (khách phản ánh).
        return Answer(head, shortlist=tuple(hit_ids))

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
