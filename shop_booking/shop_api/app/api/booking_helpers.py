from dataclasses import dataclass
from datetime import datetime, date, time, timedelta, timezone
import os
import random
import re
import string
import jwt
from flask import current_app, jsonify

from app.extensions import db
from app.models.shop import (
    Shop,
    Course,
    Addon,
    Therapist,
    Shift,
    NgList,
    Booking,
    Reservation,
    combo_restriction,
    Gender,
    BookingStatus,
)
from app.api.errors import APIError

# BR-17: cửa sổ sửa nhanh sau khi tạo booking.
EDIT_TOKEN_TTL_SECONDS = 120
# BR-16: hạn chót sửa/hủy online trước giờ hẹn.
MODIFY_DEADLINE = timedelta(hours=1)

EDIT_TOKEN_EXPIRED_MESSAGE = (
    "Phiên chỉnh sửa nhanh đã hết hạn. Vui lòng dùng trang Quản lý đặt chỗ "
    "với mã đặt chỗ và email của bạn."
)


def _secret_key() -> str:
    """Nguồn key DUY NHẤT cho cả edit_token lẫn access_token (auth_admin import lại
    hàm này). Trước đây mỗi file tự `os.environ.get(..., "dev_secret_key")` — sửa một
    chỗ mà quên chỗ kia là hai hệ token ký bằng key khác nhau, im lặng.

    create_app đã kiểm và chốt giá trị lúc boot, ở đây chỉ việc đọc ra.
    """
    return current_app.config["SECRET_KEY"]


# Token hỏng theo cách BÌNH THƯỜNG: hết hạn, sai chữ ký, chuỗi rác, chưa tới giờ
# hiệu lực, thiếu claim bắt buộc. Đây là chuyện nghiệp vụ -> 401 là đúng.
#
# CỐ Ý không bắt cả `jwt.PyJWTError` (lớp cha). Các lỗi còn lại dưới lớp cha đó —
# InvalidSubjectError, InvalidJTIError, InvalidKeyError, InvalidAlgorithmError — là
# LỖI LẬP TRÌNH của chính ta, không phải token xấu từ ngoài vào. Bắt luôn chúng rồi
# trả "Thông tin đăng nhập không đúng" là cách hiệu quả nhất để giấu bug: đúng như
# vụ `sub` là int (PyJWT >= 2.10) từng làm chết sạch endpoint admin mà không một dòng
# log nào, vì bị nuốt thành 401 trông y hệt gõ sai mật khẩu. Để chúng bay lên 500 kèm
# stack trace.
#
# An toàn vì PyJWT verify CHỮ KÝ TRƯỚC rồi mới validate claim (api_jwt.decode_complete):
# mấy lỗi kia chỉ nổ với token do chính ta ký, người ngoài không tự chế được để ép 500.
INVALID_TOKEN_ERRORS = (
    jwt.ExpiredSignatureError,
    jwt.InvalidSignatureError,
    jwt.DecodeError,
    jwt.ImmatureSignatureError,
    jwt.MissingRequiredClaimError,
)


def make_edit_token(booking_id: int) -> str:
    """Token sửa nhanh (BR-17).

    Claim `typ` là bắt buộc: edit_token và access_token của admin ký cùng SECRET_KEY,
    không phân biệt typ thì token 2 phút này cầm vào được endpoint admin (token confusion).
    """
    payload = {
        "typ": "edit",
        "booking_id": booking_id,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=EDIT_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def decode_edit_token(token: str) -> int:
    """Trả booking_id trong token. Mọi trường hợp hỏng đều là 401 EDIT_TOKEN_EXPIRED —
    không phân biệt hết hạn / sai chữ ký / sai typ, tránh làm oracle cho kẻ dò token."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
    except INVALID_TOKEN_ERRORS:
        raise APIError(401, "EDIT_TOKEN_EXPIRED", EDIT_TOKEN_EXPIRED_MESSAGE)

    if payload.get("typ") != "edit":
        raise APIError(401, "EDIT_TOKEN_EXPIRED", EDIT_TOKEN_EXPIRED_MESSAGE)

    booking_id = payload.get("booking_id")
    if not isinstance(booking_id, int):
        raise APIError(401, "EDIT_TOKEN_EXPIRED", EDIT_TOKEN_EXPIRED_MESSAGE)

    return booking_id


def time_to_minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def minutes_to_str(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def send_booking_email(booking_code: str, email: str, action: str = "create"):
    try:
        # Just mock sending email by printing or writing to log as per spec
        print(f"[EMAIL MOCK] Action: {action}, Code: {booking_code}, To: {email}")
    except Exception:
        pass


def prefetch_avail_data(shop_id: int, query_date: date, exclude_booking_id: int | None = None):
    """Gom shift + lịch bận của therapist trong ngày.

    `exclude_booking_id`: bỏ chính booking đang sửa ra khỏi "busy" (PATCH — 2.2).
    Không loại thì booking tự chiếm chỗ của chính nó: dời 14:00 sang 14:15 (chồng lấn
    với chính nó) sẽ ăn 409 SLOT_CONFLICT oan.
    """
    # Fetch shifts for the shop on date
    shifts = Shift.query.join(Therapist).filter(
        Therapist.shop_id == shop_id,
        Shift.work_date == query_date
    ).all()

    shifts_by_therapist = {}
    for s in shifts:
        shifts_by_therapist.setdefault(s.therapist_id, []).append(
            (time_to_minutes(s.start_time), time_to_minutes(s.end_time))
        )

    # Fetch active reservations for the shop on date
    reservation_q = db.session.query(Reservation).join(Booking).filter(
        Booking.shop_id == shop_id,
        Booking.booking_date == query_date,
        Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])
    )
    if exclude_booking_id is not None:
        reservation_q = reservation_q.filter(Booking.id != exclude_booking_id)
    active_reservations = reservation_q.all()

    busy_by_therapist = {}
    for r in active_reservations:
        if r.therapist_id:
            r_dur = r.main_course.duration_min + sum(a.duration_min for a in r.addons)
            start_min = time_to_minutes(r.booking.start_time)
            busy_by_therapist.setdefault(r.therapist_id, []).append(
                (start_min, start_min + r_dur)
            )

    # Get all therapists of this shop
    therapists = Therapist.query.filter_by(shop_id=shop_id).all()
    therapist_map = {t.id: t for t in therapists}

    return shifts_by_therapist, busy_by_therapist, therapist_map


def check_availability_at_time(
    shop_id: int,
    query_date: date,
    t_min: int,
    party_size: int,
    course_dur: int,
    res_addon_durs: list[int],
    therapist_id: int | None = None,
    therapist_gender: Gender | None = None,
    shifts_by_therapist = None,
    busy_by_therapist = None,
    therapist_map = None
) -> tuple[bool, list[int]]:
    """
    Checks if there's a valid assignment of therapists for the given start time t_min.
    Returns (True, assigned_therapist_ids) or (False, []).
    """
    # Find eligible therapists for each reservation
    eligible_for_res = []
    for j in range(party_size):
        r_dur = course_dur + res_addon_durs[j]
        eligible_therapists = []
        for t_id in shifts_by_therapist:
            # Check shift covering
            covered = False
            for s_start, s_end in shifts_by_therapist[t_id]:
                if s_start <= t_min and s_end >= t_min + r_dur:
                    covered = True
                    break
            if not covered:
                continue

            # Check busy overlap
            busy = False
            for b_start, b_end in busy_by_therapist.get(t_id, []):
                if max(t_min, b_start) < min(t_min + r_dur, b_end):
                    busy = True
                    break
            if not busy:
                # Apply therapist preferences (only for party_size == 1)
                if party_size == 1:
                    if therapist_id and t_id != therapist_id:
                        continue
                    if therapist_gender and therapist_map[t_id].gender != therapist_gender:
                        continue
                eligible_therapists.append(t_id)
        eligible_for_res.append(eligible_therapists)

    # Backtracking to find a one-to-one assignment of therapists
    def assign(res_index, assigned):
        if res_index == party_size:
            return True, assigned
        for t_id in eligible_for_res[res_index]:
            if t_id not in assigned:
                ok, path = assign(res_index + 1, assigned + [t_id])
                if ok:
                    return True, path
        return False, []

    ok, path = assign(0, [])
    return ok, path


def find_suggested_slots(
    shop_id: int,
    query_date: date,
    requested_start_min: int,
    party_size: int,
    course_dur: int,
    res_addon_durs: list[int],
    therapist_id: int | None = None,
    therapist_gender: Gender | None = None,
    shifts_by_therapist = None,
    busy_by_therapist = None,
    therapist_map = None
) -> list[str]:
    if not shifts_by_therapist:
        return []

    min_start = min(s[0] for s_list in shifts_by_therapist.values() for s in s_list)
    max_end = max(s[1] for s_list in shifts_by_therapist.values() for s in s_list)
    min_start = ((min_start + 14) // 15) * 15

    max_res_addon_dur = max(res_addon_durs) if res_addon_durs else 0
    max_total_dur = course_dur + max_res_addon_dur

    candidate_slots = []
    for t_min in range(min_start, max_end - max_total_dur + 1, 15):
        if t_min == requested_start_min:
            continue
        ok, _ = check_availability_at_time(
            shop_id=shop_id,
            query_date=query_date,
            t_min=t_min,
            party_size=party_size,
            course_dur=course_dur,
            res_addon_durs=res_addon_durs,
            therapist_id=therapist_id,
            therapist_gender=therapist_gender,
            shifts_by_therapist=shifts_by_therapist,
            busy_by_therapist=busy_by_therapist,
            therapist_map=therapist_map
        )
        if ok:
            candidate_slots.append(t_min)

    candidate_slots.sort(key=lambda m: abs(m - requested_start_min))
    suggested = candidate_slots[:3]
    suggested.sort()

    return [minutes_to_str(m) for m in suggested]


def get_slots_logic(shop_id, query_date, party_size, course_id, addon_ids, therapist_id=None, therapist_gender=None):
    course = Course.query.filter_by(id=course_id, shop_id=shop_id, is_active=True).first()
    if not course:
        raise APIError(422, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ chính.")

    addons = []
    if addon_ids:
        addons = Addon.query.filter(
            Addon.id.in_(addon_ids),
            Addon.shop_id == shop_id,
            Addon.is_active == True
        ).all()
        if len(addons) != len(addon_ids):
            raise APIError(422, "RESOURCE_NOT_FOUND", "Có dịch vụ bổ sung không hợp lệ.")

        restrictions = db.session.query(combo_restriction).filter(
            combo_restriction.c.course_id == course_id,
            combo_restriction.c.addon_id.in_(addon_ids)
        ).all()
        if restrictions:
            restricted_addon_id = restrictions[0].addon_id
            restricted_addon = next(a for a in addons if a.id == restricted_addon_id)
            raise APIError(
                422,
                "INVALID_COMBO",
                f"Dịch vụ {restricted_addon.name} không thể đặt kèm {course.name}. Vui lòng chọn tổ hợp khác.",
                {"course_id": course_id, "addon_id": restricted_addon_id}
            )

    shifts_by_therapist, busy_by_therapist, therapist_map = prefetch_avail_data(shop_id, query_date)

    if not shifts_by_therapist:
        return []

    min_start = min(s[0] for s_list in shifts_by_therapist.values() for s in s_list)
    max_end = max(s[1] for s_list in shifts_by_therapist.values() for s in s_list)
    min_start = ((min_start + 14) // 15) * 15

    res_addon_durs = [sum(a.duration_min for a in addons)] * party_size
    max_res_addon_dur = max(res_addon_durs) if res_addon_durs else 0
    max_total_dur = course.duration_min + max_res_addon_dur

    available_slots = []
    for t_min in range(min_start, max_end - max_total_dur + 1, 15):
        ok, _ = check_availability_at_time(
            shop_id=shop_id,
            query_date=query_date,
            t_min=t_min,
            party_size=party_size,
            course_dur=course.duration_min,
            res_addon_durs=res_addon_durs,
            therapist_id=therapist_id,
            therapist_gender=therapist_gender,
            shifts_by_therapist=shifts_by_therapist,
            busy_by_therapist=busy_by_therapist,
            therapist_map=therapist_map
        )
        if ok:
            available_slots.append(minutes_to_str(t_min))

    return available_slots


def generate_booking_code(shop_code: str, query_date: date) -> str:
    date_str = query_date.strftime("%Y%m%d")
    while True:
        rand_chars = "".join(random.choices(string.ascii_uppercase + string.digits, k=random.randint(4, 6)))
        code = f"{date_str}-{shop_code}-{rand_chars}"
        if not Booking.query.filter_by(booking_code=code).first():
            return code


@dataclass
class ValidatedBooking:
    """Payload đã qua bước 1–7 của 1.5, sẵn sàng cho transaction."""

    shop: Shop
    query_date: date
    start_time_min: int
    party_size: int
    phone: str
    email: str
    course: Course
    res_addon_ids: list[list[int]]
    res_addon_durs: list[int]
    therapist_id: int | None        # khách chỉ định ĐÍCH DANH (BR-04)
    therapist_gender: Gender | None  # khách chỉ định theo GIỚI TÍNH


def validate_booking_request(data: dict, *, check_ng_list: bool) -> ValidatedBooking:
    """Bước 1–7 của handler 1.5, dùng chung cho POST /bookings và PATCH /bookings/{code}.

    PATCH phải validate "y hệt tạo mới" (mục 2.2) — chép lại thành hai bản là hai bản
    sẽ lệch nhau dần, đúng thứ nguyên tắc "validate 2 tầng" muốn tránh. PATCH merge
    payload vào state hiện tại rồi đưa dict ĐẦY ĐỦ vào đây.

    check_ng_list: BR-06 chỉ áp lúc TẠO. Sửa không đổi được SĐT và khách đã có booking
    hợp lệ từ trước → không chặn (mục 2.2); muốn chặn thì admin hủy qua 3.6.
    """
    # --- 1. Validate format ---
    required_fields = ["shop_id", "date", "start_time", "party_size", "phone", "email", "course_id", "reservations"]
    missing = [f for f in required_fields if f not in data]
    if missing:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {f: "Missing parameter" for f in missing}})

    try:
        shop_id = int(data["shop_id"])
    except (TypeError, ValueError):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Must be an integer"}})

    shop = Shop.query.get(shop_id)
    if not shop:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    try:
        query_date = datetime.strptime(data["date"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    start_time_str = data["start_time"]
    if not isinstance(start_time_str, str) or not re.match(r"^\d{2}:\d{2}$", start_time_str):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be HH:MM"}})

    h, m = map(int, start_time_str.split(":"))
    start_time_min = h * 60 + m
    if h > 23 or m > 59 or start_time_min % 15 != 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be a valid time, multiple of 15 minutes"}})

    phone = data["phone"]
    if not isinstance(phone, str) or not re.match(r"^\d{8,15}$", phone):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"phone": "Invalid phone format"}})

    email = data["email"]
    if not isinstance(email, str) or not re.match(r"[^@]+@[^@]+\.[^@]+", email):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"email": "Invalid email format"}})

    # --- 2. party_size 1–3 và khớp len(reservations) ---
    try:
        party_size = int(data["party_size"])
        if not (1 <= party_size <= 3):
            raise ValueError()
    except (TypeError, ValueError):
        raise APIError(400, "PARTY_SIZE_EXCEEDED", "Mỗi lượt đặt tối đa 3 người. Nhóm đông hơn vui lòng liên hệ trực tiếp cửa hàng.", {"shop_phone": shop.phone})

    reservations = data["reservations"]
    if not isinstance(reservations, list) or len(reservations) != party_size:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, số lượng reservations không khớp party_size.", {"fields": {"reservations": "Mismatch with party_size"}})

    # --- 3. Nhóm ≥2 không được chỉ định therapist (BR-04) ---
    therapist_id = data.get("therapist_id")
    if therapist_id is not None:
        try:
            therapist_id = int(therapist_id)
        except (TypeError, ValueError):
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_id": "Must be an integer"}})

    therapist_gender_str = data.get("therapist_gender")
    therapist_gender = None
    if therapist_gender_str:
        if therapist_gender_str not in ["male", "female"]:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_gender": "Gender must be male or female"}})
        therapist_gender = Gender(therapist_gender_str)

    if party_size >= 2 and (therapist_id or therapist_gender):
        raise APIError(400, "THERAPIST_NOT_ALLOWED", "Đặt cho nhóm từ 2 người trở lên không thể chỉ định nhân viên.")

    if therapist_id and therapist_gender:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, không thể chọn cả nhân viên và giới tính.")

    # --- 4. course + addon tồn tại, is_active, cùng shop ---
    course = Course.query.filter_by(id=data["course_id"], shop_id=shop_id, is_active=True).first()
    if not course:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ chính.")

    res_addon_ids: list[list[int]] = []
    all_addon_ids: list[int] = []
    for j, r in enumerate(reservations):
        addon_ids = (r or {}).get("addon_ids", [])
        if not isinstance(addon_ids, list):
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, addon_ids phải là danh sách số nguyên.", {"fields": {f"reservations[{j}].addon_ids": "Must be a list"}})
        try:
            addon_ids = [int(a) for a in addon_ids]
        except (TypeError, ValueError):
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, addon_ids phải là danh sách số nguyên.", {"fields": {f"reservations[{j}].addon_ids": "Must be integers"}})
        # Bỏ trùng: [7,7] chỉ lưu được một dòng reservation_addon (PK là cặp) nhưng lại
        # cộng thời lượng hai lần -> slot bị tính dài hơn thực tế.
        addon_ids = list(dict.fromkeys(addon_ids))
        res_addon_ids.append(addon_ids)
        all_addon_ids.extend(addon_ids)

    addon_by_id: dict[int, Addon] = {}
    if all_addon_ids:
        valid_addons = Addon.query.filter(
            Addon.id.in_(set(all_addon_ids)),
            Addon.shop_id == shop_id,
            Addon.is_active == True,  # noqa: E712 — SQLAlchemy cần ==, không dùng `is`
        ).all()
        if len(valid_addons) != len(set(all_addon_ids)):
            raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ bổ sung.")
        addon_by_id = {a.id: a for a in valid_addons}

    res_addon_durs = [
        sum(addon_by_id[aid].duration_min for aid in ids) for ids in res_addon_ids
    ]

    # --- 5. Combo restriction (BR-09) ---
    if all_addon_ids:
        restrictions = db.session.query(combo_restriction).filter(
            combo_restriction.c.course_id == course.id,
            combo_restriction.c.addon_id.in_(set(all_addon_ids))
        ).all()
        if restrictions:
            restricted_addon_id = restrictions[0].addon_id
            restricted_addon = addon_by_id[restricted_addon_id]
            raise APIError(
                422,
                "INVALID_COMBO",
                f"Dịch vụ {restricted_addon.name} không thể đặt kèm {course.name}. Vui lòng chọn tổ hợp khác.",
                {"course_id": course.id, "addon_id": restricted_addon_id}
            )

    # --- 6. Phone không nằm trong ng_list (BR-06) ---
    if check_ng_list:
        ng_entry = NgList.query.filter_by(phone=phone).first()
        if ng_entry:
            raise APIError(
                403,
                "PHONE_BLOCKED",
                "Số điện thoại này hiện không thể đặt chỗ online. Vui lòng liên hệ trực tiếp cửa hàng để được hỗ trợ.",
                {"reason": ng_entry.reason or "Lý do chặn không được cung cấp.", "shop_phone": shop.phone}
            )

    # --- 7. Therapist chỉ định phải có ca phủ đủ giờ (BR-05) ---
    if therapist_id:
        therapist = Therapist.query.filter_by(id=therapist_id, shop_id=shop_id).first()
        if not therapist:
            raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy nhân viên yêu cầu.")

        # Tới đây party_size chắc chắn == 1 (bước 3 đã chặn nhóm ≥2 chỉ định).
        total_dur = course.duration_min + res_addon_durs[0]
        shifts_t = Shift.query.filter_by(therapist_id=therapist_id, work_date=query_date).all()
        covered = any(
            time_to_minutes(s.start_time) <= start_time_min
            and time_to_minutes(s.end_time) >= start_time_min + total_dur
            for s in shifts_t
        )
        if not covered:
            raise APIError(
                422,
                "THERAPIST_OFF_SHIFT",
                f"Nhân viên {therapist.name} không làm việc trong khung giờ này. Bạn có thể đổi giờ khác hoặc bỏ chỉ định.",
                {"therapist_id": therapist_id}
            )

    return ValidatedBooking(
        shop=shop,
        query_date=query_date,
        start_time_min=start_time_min,
        party_size=party_size,
        phone=phone,
        email=email,
        course=course,
        res_addon_ids=res_addon_ids,
        res_addon_durs=res_addon_durs,
        therapist_id=therapist_id,
        therapist_gender=therapist_gender,
    )


MODIFIABLE_STATUSES = (BookingStatus.PENDING, BookingStatus.CONFIRMED)


def can_modify_booking(booking: Booking) -> bool:
    """BR-16 + trạng thái. BE tính sẵn để FE chỉ việc ẩn/hiện nút — để FE tự trừ
    `date - now()` là lệch múi giờ/đồng hồ máy khách, nút hiện lên rồi bấm vào ăn 422."""
    if booking.status not in MODIFIABLE_STATUSES:
        return False
    appointment_dt = datetime.combine(booking.booking_date, booking.start_time)
    return appointment_dt - datetime.now() >= MODIFY_DEADLINE


def format_booking_response(booking: Booking, include_edit_token: bool = False):
    """Schema `Booking` dùng chung cho 1.5 / 2.1 / 2.2 / 2.3.

    `edit_token` chỉ cấp lúc TẠO (BR-17 là cửa sổ 2 phút sau khi tạo, không phải sau
    mỗi lần sửa) → mặc định tắt, chỉ POST /bookings bật lên.
    """
    shop = booking.shop
    course = None
    if booking.reservations:
        course = booking.reservations[0].main_course

    first = booking.reservations[0] if booking.reservations else None

    # PHÂN CÔNG (BR-21) — ai thực sự phục vụ. Nhóm >=2 mỗi người một người khác nhau
    # nên không gộp được về cấp booking; chỉ hiện khi đi 1 người.
    therapist_name = None
    if booking.party_size == 1 and first and first.therapist:
        therapist_name = first.therapist.name

    # CHỈ ĐỊNH (BR-04) — khách đã yêu cầu gì. Trả về đúng tên field mà PATCH nhận vào
    # để FE lấy nguyên response nhét lại vào request sửa, khỏi đoán.
    requested_therapist_id = first.requested_therapist_id if first else None
    requested_therapist_gender = (
        first.therapist_gender.value if first and first.therapist_gender else None
    )

    res_list = []
    for r in booking.reservations:
        res_list.append({
            "guest_no": r.guest_no,
            "addons": [{
                "id": a.id,
                "name": a.name,
                "duration_min": a.duration_min,
                "price": a.price
            } for a in r.addons]
        })

    body = {
        "booking_code": booking.booking_code,
        "status": booking.status.value,
        "shop": {
            "id": shop.id,
            "shop_code": shop.shop_code,
            "name": shop.name,
            "address": shop.address,
            "phone": shop.phone
        },
        "date": booking.booking_date.strftime("%Y-%m-%d"),
        "start_time": booking.start_time.strftime("%H:%M"),
        "party_size": booking.party_size,
        "course": {
            "id": course.id,
            "name": course.name,
            "duration_min": course.duration_min,
            "price": course.price
        } if course else None,
        "reservations": res_list,
        "therapist_name": therapist_name,
        "requested_therapist_id": requested_therapist_id,
        "requested_therapist_gender": requested_therapist_gender,
        "can_modify": can_modify_booking(booking),
    }

    if include_edit_token:
        body["edit_token"] = make_edit_token(booking.id)
        body["edit_token_expires_in"] = EDIT_TOKEN_TTL_SECONDS

    return jsonify(body)