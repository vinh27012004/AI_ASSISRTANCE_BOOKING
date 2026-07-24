"""Dựng nút lựa chọn cho widget (§7). `value` là token nút -> state_machine.apply_button
xử lý tất định, không qua LLM (giảm NLU sai — §10).
"""

from __future__ import annotations

from datetime import date, timedelta

from app.session import Session
from app import states as S


def buttons_for(state: str, session: Session, api_result: dict) -> list[dict]:
    ar = api_result or {}

    if state == S.SHOP:
        return [{"label": sh["name"], "value": f"shop:{sh['id']}"} for sh in ar.get("shops", [])]

    if state == S.DATE:
        out = []
        for i in range(4):
            d = date.today() + timedelta(days=i)
            label = {0: "Hôm nay", 1: "Ngày mai"}.get(i, d.strftime("%d/%m"))
            out.append({"label": label, "value": f"date:{d.isoformat()}"})
        return out

    if state == S.PARTY_SIZE:
        return [{"label": f"{n} người", "value": f"party:{n}"} for n in (1, 2, 3)]

    if state == S.COURSE:
        # Course đã kèm sẵn thời lượng -> hiện luôn trên nhãn, khỏi hỏi "bao nhiêu phút".
        return [{"label": f"{c['name']} · {c['duration_min']}'", "value": f"course:{c['id']}"}
                for c in ar.get("courses", [])]

    if state == S.ADDON:
        # Bước RIÊNG sau khi đã chọn course: toggle add-on + chốt. Add-on cấm với course
        # đang chọn (BR-09) bị ẩn để không mời nhầm (A3 sớm).
        chosen = session.slots.course_id
        selected = set(session.slots.addons)
        out: list[dict] = []
        for a in ar.get("addons", []):
            if chosen and chosen in a.get("restricted_course_ids", []):
                continue
            mark = "✓ " if a["id"] in selected else "+ "
            out.append({"label": f"{mark}{a['name']} · {a['duration_min']}'", "value": f"addon:{a['id']}"})
        out.append({"label": "Không thêm", "value": "addon:none"})
        if selected:
            out.append({"label": "Xong", "value": "addon:done"})
        return out

    if state == S.SLOT:
        times = ar.get("slots") or ar.get("suggested_slots") or []
        return [{"label": t, "value": f"slot:{t}"} for t in times]

    if state == S.THERAPIST:
        out = [{"label": t["name"], "value": f"therapist:{t['id']}"}
               for t in ar.get("therapists", [])]
        out += [
            {"label": "Nhân viên nam", "value": "therapist:male"},
            {"label": "Nhân viên nữ", "value": "therapist:female"},
            {"label": "Để cửa hàng sắp", "value": "therapist:skip"},
        ]
        return out

    if state == S.CONFIRM:
        return [
            {"label": "Đồng ý đặt", "value": "confirm:yes"},
            {"label": "Sửa lại", "value": "confirm:no"},
        ]

    if state == S.DONE:
        return [
            {"label": "✏️ Sửa lịch", "value": "modify:start"},
            {"label": "🗑 Hủy lịch", "value": "cancel:start"},
        ]

    if state == S.MODIFY:
        return [
            {"label": "Đổi giờ", "value": "modify:slot"},
            {"label": "Đổi số người", "value": "modify:party"},
            {"label": "Đổi dịch vụ", "value": "modify:course"},
            {"label": "🗑 Hủy lịch", "value": "cancel:start"},
            {"label": "Giữ nguyên", "value": "modify:keep"},
        ]

    if state in (S.END, "HANDOFF"):
        phone = ar.get("shop_phone") or session.shop_phone
        label = f"📞 Gọi cửa hàng {phone}" if phone else "📞 Gọi cửa hàng"
        return [{"label": label, "value": "handoff:call"}]

    return []
