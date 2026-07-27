"""Dựng nút lựa chọn cho widget (§7). `value` là token nút -> state_machine.apply_button
xử lý tất định, không qua LLM (giảm NLU sai — §10).
"""

from __future__ import annotations

from datetime import date, timedelta

from app.session import Session
from app import states as S

# Tối đa số nút ngày hiển thị (danh sách ngày active có thể dài) — dư thì khách gõ tay.
DATE_BUTTON_LIMIT = 8


def _date_button(iso: str) -> dict:
    """Nút một ngày: nhãn 'Hôm nay'/'Ngày mai' cho gần, còn lại 'dd/mm'."""
    d = date.fromisoformat(iso)
    delta = (d - date.today()).days
    label = {0: "Hôm nay", 1: "Ngày mai"}.get(delta) or d.strftime("%d/%m")
    return {"label": label, "value": f"date:{iso}"}


def buttons_for(state: str, session: Session, api_result: dict) -> list[dict]:
    ar = api_result or {}

    if state == S.SHOP:
        return [{"label": sh["name"], "value": f"shop:{sh['id']}"} for sh in ar.get("shops", [])]

    if state == S.DATE:
        # Nếu đã dò được ngày mở cửa của shop (active_dates) thì CHỈ mời những ngày đó —
        # khỏi để khách chọn nhằm ngày shop nghỉ. Danh sách dài thì cắt bớt (còn lại khách
        # gõ tay được, orchestrator hiểu "31", "31/7"…). active_dates rỗng đã được
        # orchestrator xử lý (quay lại chọn shop) nên ở đây không vẽ nút.
        active = ar.get("active_dates")
        if active is not None:
            return [_date_button(iso) for iso in active[:DATE_BUTTON_LIMIT]]
        # Chưa dò được (API lỗi) -> mặc định hôm nay..+3 như cũ.
        return [_date_button((date.today() + timedelta(days=i)).isoformat()) for i in range(4)]

    if state == S.PARTY_SIZE:
        return [{"label": f"{n} người", "value": f"party:{n}"} for n in (1, 2, 3)]

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
        out: list[dict] = []
        for a in ar.get("addons", []):
            if chosen and chosen in a.get("restricted_course_ids", []):
                continue
            mark = "✓ " if a["id"] in selected else "+ "
            out.append({"label": f"{mark}{a['name']} · {a['duration_min']}'", "value": f"addon:{a['id']}"})
        out.append({"label": "Không thêm", "value": "addon:none"})
        if selected:
            last = idx >= (s.party_size or 1) - 1
            out.append({"label": "Xong" if last else "Người tiếp theo →", "value": "addon:done"})
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
