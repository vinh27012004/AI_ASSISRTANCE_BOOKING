"""Dựng nút lựa chọn cho widget (§7). `value` là token nút -> state_machine.apply_button
xử lý tất định, không qua LLM (giảm NLU sai — §10).

Nhãn nút bản địa hoá theo `session.lang` (vi/en/ja) — `value` GIỮ NGUYÊN (token tất định).
Tên shop/course/nhân viên, giờ, dd/mm là DỮ LIỆU nên không dịch.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.session import Session
from app import states as S

# Tối đa số nút ngày hiển thị (danh sách ngày active có thể dài) — dư thì khách gõ tay.
DATE_BUTTON_LIMIT = 8

# Nhãn tĩnh theo ngôn ngữ. Thiếu ngôn ngữ -> lùi về 'vi'.
_L = {
    "today":       {"vi": "Hôm nay", "en": "Today", "ja": "今日"},
    "tomorrow":    {"vi": "Ngày mai", "en": "Tomorrow", "ja": "明日"},
    "party":       {"vi": "{n} người", "en": "{n} people", "ja": "{n}名"},
    "party_one":   {"vi": "{n} người", "en": "{n} person", "ja": "{n}名"},   # tiếng Anh số ít
    "who":         {"vi": " người {k}", "en": " (guest {k})", "ja": "（{k}人目）"},
    "addon_done":  {"vi": "✅ Xong{who} →", "en": "✅ Done{who} →", "ja": "✅ 完了{who} →"},
    "addon_none":  {"vi": "Không thêm{who} →", "en": "No add-on{who} →", "ja": "追加なし{who} →"},
    "th_male":     {"vi": "Nhân viên nam", "en": "Male therapist", "ja": "男性スタッフ"},
    "th_female":   {"vi": "Nhân viên nữ", "en": "Female therapist", "ja": "女性スタッフ"},
    "th_any":      {"vi": "Để cửa hàng sắp", "en": "Let the shop choose", "ja": "おまかせ"},
    "confirm_yes": {"vi": "Đồng ý đặt", "en": "Confirm booking", "ja": "予約を確定"},
    "confirm_no":  {"vi": "Sửa lại", "en": "Edit", "ja": "修正する"},
    "do_modify":   {"vi": "✏️ Sửa lịch", "en": "✏️ Edit booking", "ja": "✏️ 変更する"},
    "do_cancel":   {"vi": "🗑 Hủy lịch", "en": "🗑 Cancel booking", "ja": "🗑 キャンセル"},
    "mod_slot":    {"vi": "Đổi giờ", "en": "Change time", "ja": "時間を変更"},
    "mod_party":   {"vi": "Đổi số người", "en": "Change party size", "ja": "人数を変更"},
    "mod_course":  {"vi": "Đổi dịch vụ", "en": "Change service", "ja": "コースを変更"},
    "mod_keep":    {"vi": "Giữ nguyên", "en": "Keep as is", "ja": "変更しない"},
    "call":        {"vi": "📞 Gọi cửa hàng {phone}", "en": "📞 Call the shop {phone}",
                    "ja": "📞 店舗に電話 {phone}"},
    "call_only":   {"vi": "📞 Gọi cửa hàng", "en": "📞 Call the shop", "ja": "📞 店舗に電話"},
}


def _t(lang: str, key: str, **kw) -> str:
    tmpl = _L[key].get(lang) or _L[key]["vi"]
    return tmpl.format(**kw) if kw else tmpl


def _date_button(iso: str, lang: str) -> dict:
    """Nút một ngày: nhãn 'Hôm nay'/'Ngày mai' cho gần, còn lại 'dd/mm'."""
    d = date.fromisoformat(iso)
    delta = (d - date.today()).days
    if delta == 0:
        label = _t(lang, "today")
    elif delta == 1:
        label = _t(lang, "tomorrow")
    else:
        label = d.strftime("%d/%m")
    return {"label": label, "value": f"date:{iso}"}


def buttons_for(state: str, session: Session, api_result: dict) -> list[dict]:
    ar = api_result or {}
    lang = session.lang if session.lang in ("vi", "en", "ja") else "vi"

    if state == S.SHOP:
        return [{"label": sh["name"], "value": f"shop:{sh['id']}"} for sh in ar.get("shops", [])]

    if state == S.DATE:
        # Nếu đã dò được ngày mở cửa của shop (active_dates) thì CHỈ mời những ngày đó —
        # khỏi để khách chọn nhằm ngày shop nghỉ. Danh sách dài thì cắt bớt (còn lại khách
        # gõ tay được, orchestrator hiểu "31", "31/7"…). active_dates rỗng đã được
        # orchestrator xử lý (quay lại chọn shop) nên ở đây không vẽ nút.
        active = ar.get("active_dates")
        if active is not None:
            return [_date_button(iso, lang) for iso in active[:DATE_BUTTON_LIMIT]]
        # Chưa dò được (API lỗi) -> mặc định hôm nay..+3 như cũ.
        return [_date_button((date.today() + timedelta(days=i)).isoformat(), lang) for i in range(4)]

    if state == S.PARTY_SIZE:
        return [{"label": _t(lang, "party_one" if n == 1 else "party", n=n), "value": f"party:{n}"}
                for n in (1, 2, 3)]

    if state == S.COURSE:
        # Course đã kèm sẵn thời lượng -> hiện luôn trên nhãn, khỏi hỏi "bao nhiêu phút".
        return [{"label": f"{c['name']} · {c['duration_min']}'", "value": f"course:{c['id']}"}
                for c in ar.get("courses", [])]

    if state == S.ADDON:
        # Bước RIÊNG sau khi chọn course: add-on RIÊNG từng người (BR-10). Add-on cấm với
        # course đang chọn (BR-09) bị ẩn để không mời nhầm (A3 sớm).
        s = session.slots
        s.ensure_guest_addons()
        idx = min(s.addon_guest_idx, len(s.guest_addons) - 1)
        chosen = s.course_id
        selected = set(s.guest_addons[idx])
        n = s.party_size or 1
        out: list[dict] = []
        for a in ar.get("addons", []):
            if chosen and chosen in a.get("restricted_course_ids", []):
                continue
            mark = "✓ " if a["id"] in selected else "+ "
            out.append({"label": f"{mark}{a['name']} · {a['duration_min']}'", "value": f"addon:{a['id']}"})
        # ĐÚNG MỘT nút đi tiếp (không để lẫn "Không thêm" + "Xong" cùng lúc → khách khỏi kẹt
        # bấm toggle mãi): đã chọn -> "✅ Xong[ người k] →" (giữ lựa chọn); chưa chọn -> "Không
        # thêm[ người k] →" (bỏ qua người này). Kèm số người khi đặt nhóm cho rõ đang ở đâu.
        who = _t(lang, "who", k=idx + 1) if n > 1 else ""
        if selected:
            out.append({"label": _t(lang, "addon_done", who=who), "value": "addon:done"})
        else:
            out.append({"label": _t(lang, "addon_none", who=who), "value": "addon:none"})
        return out

    if state == S.SLOT:
        times = ar.get("slots") or ar.get("suggested_slots") or []
        return [{"label": t, "value": f"slot:{t}"} for t in times]

    if state == S.THERAPIST:
        out = [{"label": t["name"], "value": f"therapist:{t['id']}"}
               for t in ar.get("therapists", [])]
        out += [
            {"label": _t(lang, "th_male"), "value": "therapist:male"},
            {"label": _t(lang, "th_female"), "value": "therapist:female"},
            {"label": _t(lang, "th_any"), "value": "therapist:skip"},
        ]
        return out

    if state == S.CONFIRM:
        return [
            {"label": _t(lang, "confirm_yes"), "value": "confirm:yes"},
            {"label": _t(lang, "confirm_no"), "value": "confirm:no"},
        ]

    if state == S.DONE:
        return [
            {"label": _t(lang, "do_modify"), "value": "modify:start"},
            {"label": _t(lang, "do_cancel"), "value": "cancel:start"},
        ]

    if state == S.MODIFY:
        return [
            {"label": _t(lang, "mod_slot"), "value": "modify:slot"},
            {"label": _t(lang, "mod_party"), "value": "modify:party"},
            {"label": _t(lang, "mod_course"), "value": "modify:course"},
            {"label": _t(lang, "do_cancel"), "value": "cancel:start"},
            {"label": _t(lang, "mod_keep"), "value": "modify:keep"},
        ]

    if state in (S.END, "HANDOFF"):
        phone = ar.get("shop_phone") or session.shop_phone
        label = _t(lang, "call", phone=phone) if phone else _t(lang, "call_only")
        return [{"label": label, "value": "handoff:call"}]

    return []
