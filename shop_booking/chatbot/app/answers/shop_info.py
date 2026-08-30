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


def _hhmm(m: int) -> str:
    return f"{(m // 60) % 24:02d}:{m % 60:02d}"


def _plus12(t: str) -> str | None:
    """'07:00' -> '19:00'. Dùng khi khách nói '7h' trần (không rõ sáng hay tối)."""
    try:
        m = _mins(t) + 12 * 60
    except (ValueError, IndexError):
        return None
    return _hhmm(m) if m < 24 * 60 else None


def _today() -> str:
    return _date.today().isoformat()


def _resolve_shop(ctx: QueryCtx, shops: list[dict]) -> dict | None:
    """Cửa hàng khách đang hỏi: tên NLU trích -> tên nằm trong câu gốc -> shop đang chọn
    trong phiên -> chịu (hỏi lại).

    Lùi về câu gốc vì nhánh rule-based (chưa cấu hình LLM) không biết tên cửa hàng — chúng
    đến từ API. pick_unique khớp chuỗi-con hai chiều nên "Cửa hàng Hải Châu ở đâu?" trúng
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

def _spans_at(api, shop_id: int, day: str, t: str) -> list[int]:
    """Mỗi nhân viên CÓ CA phủ giờ `t`: còn bao nhiêu phút nữa mới tan ca. Sắp GIẢM DẦN.

    Bảng shop KHÔNG có open_time/close_time (models/shop.py) — trong hệ này "mở cửa" =
    CÓ NHÂN VIÊN CÓ CA, đúng định nghĩa mà GET /availability dùng. GET /timeline trả
    therapists[].shifts[] nên từ MỘT danh sách này đọc được cả hai thứ: giờ đó có ai trực
    không (rỗng hay không), và còn phục vụ được lượt dài bao nhiêu phút cho nhóm mấy người
    (phần tử thứ n-1 — nhóm n người cần n nhân viên CÙNG LÚC)."""
    data = api.get_timeline(shop_id, day)
    try:
        want = _mins(t)
    except (ValueError, IndexError):
        return []
    out = []
    for th in data.get("therapists", []):
        rem = 0
        for sh in th.get("shifts", []):
            try:
                if _mins(sh["start_time"]) <= want < _mins(sh["end_time"]):
                    rem = max(rem, _mins(sh["end_time"]) - want)
            except (ValueError, KeyError, IndexError):
                continue
        if rem:
            out.append(rem)
    out.sort(reverse=True)
    return out


def _budget(spans: list[int], party: int) -> int:
    """Quỹ phút cho `party` người cùng lúc — 0 nếu không đủ người trực."""
    return spans[party - 1] if len(spans) >= party else 0


def _has_shift_at(api, shop_id: int, day: str, t: str) -> bool:
    """Có nhân viên nào đang trong ca lúc `t` không."""
    return bool(_spans_at(api, shop_id, day, t))


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
    return _hhmm(lo), _hhmm(hi)


# "mở cửa từ mấy giờ tới mấy giờ", "mấy giờ đóng cửa", "giờ làm việc thế nào" — khách hỏi
# KHUNG GIỜ, không hỏi "chỗ nào còn sáng đèn". Cùng question_type=shops_open_at nên phải
# phân biệt bằng chính lời khách.
_ASK_HOURS_RE = re.compile(
    r"mấy giờ|may gio|lúc nào|luc nao|giờ (?:mở|đóng|làm|mo|dong|lam)"
    r"|(?:mở|đóng|mo|dong)\s*cửa\s*(?:từ|đến|tới|lúc|tu|den|toi|luc)"
    r"|giờ giấc|gio giac|khung giờ làm|khung gio lam")


def _hours_answer(api, shops: list[dict], day: str, who: str) -> Answer:
    """Khung giờ làm của `shops` trong ngày. `who` là chủ ngữ đọc lên cho tự nhiên."""
    hours = _day_hours(api, shops, day)
    if not hours:
        return Answer(f"Dạ ngày {_d(day)} {who} không có ca làm nào ạ. "
                      "Anh/chị thử hỏi giúp em ngày khác nhé.")
    return Answer(f"Dạ ngày {_d(day)} {who} làm từ {hours[0]} đến {hours[1]} ạ.",
                  shortlist=tuple(s["id"] for s in shops))


def _shops_open_on(api, day: str) -> Answer:
    """Cửa hàng CÓ LÀM trong ngày (có ít nhất một ca) — không xét giờ cụ thể."""
    hits = [s for s in api.get_shops() if _shifts(api, s["id"], day)]
    if not hits:
        return Answer(f"Dạ ngày {_d(day)} không cửa hàng nào có lịch làm ạ. "
                      "Anh/chị thử hỏi giúp em ngày khác nhé.")
    return Answer(f"Ngày {_d(day)} các cửa hàng có làm là: "
                  f"{', '.join(s['name'] for s in hits)} ạ.",
                  shortlist=tuple(s["id"] for s in hits))


# --------------------------------------------------------------------------- #
#  Đã chốt gói -> "mở lúc 19h" phải hiểu là "NHẬN ĐƯỢC GÓI NÀY lúc 19h"        #
# --------------------------------------------------------------------------- #
#
# Bug thật trong log: bước chọn giờ vừa báo "19:00 không còn trống" ở Cửa hàng Hải Châu thì
# ngay lượt sau, câu "kiếm giúp cửa hàng nào mở lúc 7h tối" lại kể tên chính cửa hàng đó.
# Cùng một dữ liệu mà hai câu ngược nhau, vì câu hỏi trước đây chỉ xét CÓ AI TRỰC lúc
# 19:00. Gói Massage tinh dầu 90 kèm 3 add-on dài 135 phút: bắt đầu 19:00 thì 21:15 mới xong,
# muộn hơn giờ tan ca của mọi nhân viên -> có người trực vẫn không nhận được.

_TRY_LIMIT = 3          # số gói thay thế tối đa đem đi hỏi /slots (đây chỉ là câu gợi ý)


def _wanted_party(ctx: QueryCtx) -> int:
    try:
        return max(int(ctx.entities.get("party_size") or ctx.party_size or 1), 1)
    except (TypeError, ValueError):
        return 1


def _upcoming(slots: list[str], day: str) -> list[str]:
    """Bỏ giờ ĐÃ QUA nếu hỏi cho HÔM NAY (cùng luật Orchestrator._future_slots)."""
    from datetime import datetime as _dt

    if day != _today():
        return slots
    now = _dt.now().hour * 60 + _dt.now().minute
    out = []
    for t in slots:
        try:
            if _mins(t) >= now:
                out.append(t)
        except (ValueError, IndexError):
            continue
    return out


def _slots_of(api, shop_id: int, day: str, party: int, combo: dict) -> list[str]:
    """Giờ ĐẶT ĐƯỢC thật cho gói này. Hỏi thẳng GET /slots vì chỉ BE mới tính đủ ba thứ:
    ca làm, khoảng đã có khách đặt, và thời lượng gói phải nằm gọn trong ca."""
    data = api.get_slots(shop_id, date=day, party_size=party,
                         course_id=combo["course"]["id"],
                         addon_ids=[a["id"] for a in combo["addons"]])
    return _upcoming(list(data.get("slots") or []), day)


def _combo_at(api, shop_id: int, day: str, party: int,
              course_name: str, addon_names: tuple[str, ...]) -> dict | None:
    """Gói khách đã chốt, DỊCH sang catalog của một cửa hàng khác.

    id course/add-on là riêng từng shop nên phải khớp lại theo TÊN. None = shop đó không
    có gói này. Add-on shop không bán, hoặc bị cấm kèm gói này (BR-09), rơi vào `missing`
    để câu trả lời nói thật chứ không lặng lẽ bỏ bớt rồi hứa suông."""
    data = api.get_services(shop_id, day, party)
    courses = data.get("courses") or []
    addons = data.get("addons") or []
    course = next((c for c in courses
                   if matching.name_matches(course_name, c.get("name") or "")), None)
    if course is None:
        return None
    picked, missing = [], []
    for nm in addon_names:
        a = next((x for x in addons
                  if matching.name_matches(nm, x.get("name") or "")), None)
        if a and course.get("id") not in (a.get("restricted_course_ids") or []):
            picked.append(a)
        else:
            missing.append(nm)
    return {"course": course, "addons": picked, "missing": missing,
            "minutes": int(course.get("duration_min") or 0)
            + sum(int(a.get("duration_min") or 0) for a in picked)}


def _combo_label(combo: dict) -> str:
    ten = combo["course"].get("name") or ""
    if combo["addons"]:
        ten += " + " + ", ".join(a.get("name") or "" for a in combo["addons"])
    return f"{ten} ({combo['minutes']} phút)"


def _bookable_within(api, shop: dict, day: str, party: int, budget: int,
                     addon_names: tuple[str, ...], t: str) -> dict | None:
    """Gói DÀI NHẤT vừa quỹ giờ `budget` mà /slots thật sự nhận lúc `t`.

    Ưu tiên giữ add-on khách đã chọn, không vừa mới bỏ add-on rồi mới hạ gói — khách đã
    nói ra thì đừng tự ý cắt. Chỉ thử `_TRY_LIMIT` gói: đây là câu gợi ý, không đáng dò
    cả bảng giá."""
    data = api.get_services(shop["id"], day, party)
    courses = data.get("courses") or []
    addons = data.get("addons") or []
    keep = [a for a in addons
            if any(matching.name_matches(n, a.get("name") or "") for n in addon_names)]
    tried = 0
    for pack in ([keep, []] if keep else [[]]):
        extra = sum(int(a.get("duration_min") or 0) for a in pack)
        fit = [c for c in courses
               if int(c.get("duration_min") or 0) + extra <= budget
               and not any(c.get("id") in (a.get("restricted_course_ids") or [])
                           for a in pack)]
        for c in sorted(fit, key=lambda x: int(x.get("duration_min") or 0), reverse=True):
            combo = {"course": c, "addons": pack, "missing": [],
                     "minutes": int(c.get("duration_min") or 0) + extra}
            if t in _slots_of(api, shop["id"], day, party, combo):
                return combo
            tried += 1
            if tried >= _TRY_LIMIT:
                return None
    return None


def _shops_serving_at(ctx: QueryCtx, api, shops: list[dict], day: str,
                      times: list[str], party: int) -> Answer:
    """"Cửa hàng nào còn mở lúc 19h?" khi tờ đơn ĐÃ có gói dịch vụ."""
    course_name = ctx.course_name or ""
    # Khảo sát một lần: shop có gói này không, lúc đó quỹ giờ còn bao nhiêu, /slots có
    # nhận không. Lọc bằng quỹ giờ TRƯỚC nên chỉ shop còn cơ hội mới tốn lời gọi /slots.
    survey: dict[str, list[tuple]] = {}
    for tt in times:
        rows = []
        for s in shops:
            combo = _combo_at(api, s["id"], day, party, course_name, ctx.addon_names)
            if combo is None:
                continue                   # shop không có gói này -> không so được, bỏ
            spans = _spans_at(api, s["id"], day, tt)
            ok = (_budget(spans, party) >= combo["minutes"]
                  and tt in _slots_of(api, s["id"], day, party, combo))
            rows.append((s, combo, spans, ok))
        survey[tt] = rows

    parts, hit_ids, shown = [], [], None
    for tt in times:
        hits = [(s, c) for s, c, _sp, ok in survey[tt] if ok]
        if not hits:
            continue
        shown = shown or hits[0][1]
        names = []
        for s, c in hits:
            thieu = (f"; bên này chưa kèm được {', '.join(c['missing'])}"
                     if c["missing"] else "")
            names.append(f"{s['name']} (xong khoảng "
                         f"{_hhmm(_mins(tt) + c['minutes'])}{thieu})")
        parts.append(f"lúc {tt} đặt được ở {', '.join(names)}")
        hit_ids += [s["id"] for s, _c in hits if s["id"] not in hit_ids]
    if parts:
        return Answer(f"Ngày {_d(day)} với gói {_combo_label(shown)}, "
                      f"{'; '.join(parts)} ạ.", shortlist=tuple(hit_ids))

    # Không nơi nào nhận -> nói RÕ vướng ở đâu rồi mới gợi ý, đừng bắt khách tự đoán.
    # Lấy giờ còn dư nhiều nhất trong các giờ đã hỏi ("7h" trần hỏi cả 07:00 lẫn 19:00).
    tt = max(times, key=lambda x: max([_budget(sp, party)
                                       for _s, _c, sp, _o in survey[x]] or [0]))
    rows = survey[tt]
    if not rows:
        return Answer(f"Dạ ngày {_d(day)} em chưa thấy cửa hàng nào có gói anh/chị đã "
                      "chọn ạ. Anh/chị chọn giúp em gói khác nhé.")

    mine = next((c for s, c, _sp, _o in rows if s["id"] == ctx.shop_id), None) or rows[0][1]
    need = mine["minutes"]
    staff = max(len(sp) for _s, _c, sp, _o in rows)
    best = max(_budget(sp, party) for _s, _c, sp, _o in rows)

    head = f"Ngày {_d(day)} lúc {tt} chưa cửa hàng nào nhận được gói {_combo_label(mine)}"
    if staff == 0:
        head += " ạ — giờ đó không cửa hàng nào có nhân viên trực."
    elif staff < party:
        head += (f" ạ — lúc đó nhiều nhất chỉ {staff} nhân viên cùng trực, mà anh/chị "
                 f"đang đặt {party} người.")
    elif best < need:
        # Đây chính là điều khách hỏi: gói phải XONG trước lúc nhân viên tan ca.
        head += (f" ạ — nhân viên trực lúc đó chỉ còn {best} phút nữa là tan ca "
                 f"({_hhmm(_mins(tt) + best)}), trong khi gói cần {need} phút, bắt đầu "
                 f"{tt} thì {_hhmm(_mins(tt) + need)} mới xong.")
    else:
        head += " ạ — nhân viên giờ đó đã kín khách."

    tips = []
    cand = max(rows, key=lambda r: _budget(r[2], party))
    room = _budget(cand[2], party)
    if room > 0:
        alt = _bookable_within(api, cand[0], day, party, room, ctx.addon_names, tt)
        if alt:
            tips.append(f"Lúc {tt} thì {cand[0]['name']} nhận được gói {_combo_label(alt)}, "
                        f"xong lúc {_hhmm(_mins(tt) + alt['minutes'])} — vừa kịp giờ nhân "
                        "viên tan ca ạ.")
    latest = None
    for s, combo, _sp, _o in rows:
        sl = _slots_of(api, s["id"], day, party, combo)
        if sl:
            top = max(sl, key=_mins)
            if latest is None or _mins(top) > _mins(latest[1]):
                latest = (s, top)
    if latest:
        tips.append(f"Còn nếu giữ nguyên gói thì ngày {_d(day)} giờ bắt đầu muộn nhất là "
                    f"{latest[1]} ở {latest[0]['name']} ạ.")

    return Answer(" ".join([head] + tips),
                  shortlist=tuple(s["id"] for s, _c, _sp, _o in rows))


def shops_open_at(ctx: QueryCtx, api) -> Answer:
    t = ctx.entities.get("time")
    day = ctx.entities.get("date") or ctx.date or _today()
    if not t:
        # Hỏi KHUNG GIỜ ("Hải Châu mở từ mấy giờ tới mấy giờ?") -> đọc giờ mở/đóng. Trước
        # đây rơi hết vào _shops_open_on nên bot đáp bằng DANH SÁCH cửa hàng có làm — không
        # dính gì tới câu hỏi, và trùng y nguyên câu lượt trước (bug thật trong log).
        if _ASK_HOURS_RE.search((ctx.raw_text or "").lower()):
            sh = _resolve_shop(ctx, api.get_shops())
            if sh is not None:
                return _hours_answer(api, [sh], day, sh["name"])
            return _hours_answer(api, api.get_shops(), day, "bên em")
        # "Cửa hàng nào đang mở hôm nay?" — hỏi theo NGÀY chứ không theo giờ. Hỏi ngược
        # "khung giờ nào ạ?" là né câu hỏi (khách phản ánh); trả lời thẳng theo ngày.
        return _shops_open_on(api, day)
    times = [t]
    if ctx.time_ambiguous:                      # "7h" trần -> trả lời cả 07:00 lẫn 19:00
        alt = _plus12(t)
        if alt:
            times.append(alt)

    shops = _scope(ctx, api.get_shops())
    # Đã chốt gói -> "mở cửa" không còn đủ, gói phải nhét vừa phần ca còn lại của nhân
    # viên (xem khối chú thích ở trên).
    if ctx.course_name:
        return _shops_serving_at(ctx, api, shops, day, times, _wanted_party(ctx))

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
    items = ", ".join(f"{c.get('name')} · {c.get('duration_min')} phút · {nlg.format_price(c.get('price'))}"
                      for c in courses)
    return Answer(f"Dạ bảng giá {sh['name']} ạ: {items}.")
