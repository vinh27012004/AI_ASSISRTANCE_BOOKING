from flask import jsonify, request
from datetime import datetime, date, time, timedelta
import jwt
import re
import os

from app.extensions import db
from app.models.shop import (
    Shop,
    Course,
    Addon,
    Therapist,
    Shift,
    Customer,
    NgList,
    TimeSlot,
    Booking,
    Reservation,
    combo_restriction,
    Gender,
    SlotStatus,
    BookingStatus,
)
from app.api import api_bp
from app.api.errors import APIError
from app.api.rate_limit import rate_limit
from app.api.booking_helpers import (
    time_to_minutes,
    send_booking_email,
    prefetch_avail_data,
    check_availability_at_time,
    find_suggested_slots,
    generate_booking_code,
    format_booking_response,
    make_edit_token,
    decode_edit_token,
    can_modify_booking,
)


# ============================================================
# Helper: verify edit token or email+booking_code
# ============================================================
def verify_booking_access(booking_code: str, request_data: dict, edit_token_header: str | None):
    """
    Returns (booking, used_edit_token: bool)
    Raises APIError if not authorized.
    """
    # Normalize booking_code
    booking_code = booking_code.strip().upper()
    # Find booking by code
    booking = Booking.query.filter_by(booking_code=booking_code).first()
    if not booking:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")

    # Check edit token first
    if edit_token_header:
        try:
            booking_id_from_token = decode_edit_token(edit_token_header)
            if booking_id_from_token != booking.id:
                raise APIError(401, "EDIT_TOKEN_EXPIRED", "Phiên chỉnh sửa nhanh đã hết hạn. Vui lòng dùng trang Quản lý đặt chỗ với mã đặt chỗ và email của bạn.")
            # Token valid for this booking
            return booking, True
        except APIError:
            raise

    # Fallback: email in body
    email = request_data.get("email")
    if not email:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")
    email = email.strip().lower()
    if booking.customer.email.strip().lower() != email:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")
    return booking, False


# ============================================================
# 2.1 POST /bookings/retrieve
# ============================================================
@api_bp.route("/bookings/retrieve", methods=["POST"])
@rate_limit(10, 60)
def retrieve_booking():
    data = request.get_json() or {}
    booking_code = data.get("booking_code")
    email = data.get("email")

    if not booking_code or not email:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"booking_code": "Missing", "email": "Missing"}})

    booking_code = booking_code.strip().upper()
    email = email.strip().lower()

    booking = Booking.query.filter_by(booking_code=booking_code).first()
    if not booking:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")
    if booking.customer.email.strip().lower() != email:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")

    can_modify = can_modify_booking(booking)

    # Use format_booking_response but without edit_token
    response = format_booking_response(booking).get_json()
    response["can_modify"] = can_modify
    # Remove edit_token if present
    response.pop("edit_token", None)
    response.pop("edit_token_expires_in", None)

    return jsonify(response), 200


# ============================================================
# 2.2 PATCH /bookings/{bookingCode}
# ============================================================
@api_bp.route("/bookings/<string:booking_code>", methods=["PATCH"])
@rate_limit(10, 60)
def update_booking(booking_code):
    data = request.get_json() or {}
    edit_token = request.headers.get("X-Edit-Token")

    # Verify access
    booking, used_edit_token = verify_booking_access(booking_code, data, edit_token)

    # Check status
    if booking.status not in (BookingStatus.PENDING, BookingStatus.CONFIRMED):
        raise APIError(422, "INVALID_STATUS_TRANSITION", "Không thể chuyển trạng thái này.", {"from": booking.status.value, "to": "modified"})

    # Deadline check for current appointment
    if not can_modify_booking(booking):
        raise APIError(422, "MODIFY_DEADLINE_PASSED", "Đã quá thời hạn thay đổi online (trước giờ hẹn 1 tiếng). Vui lòng gọi trực tiếp cửa hàng.", {"shop_phone": booking.shop.phone})

    # Prepare new values (merge with existing)
    new_date = data.get("date", booking.booking_date.strftime("%Y-%m-%d"))
    new_start_time = data.get("start_time", booking.start_time.strftime("%H:%M"))
    new_party_size = data.get("party_size", booking.party_size)
    new_course_id = data.get("course_id")
    new_reservations = data.get("reservations")
    new_therapist_id = data.get("therapist_id")
    new_therapist_gender_str = data.get("therapist_gender")

    # Validate date
    try:
        query_date = datetime.strptime(new_date, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    # Validate start_time
    if not re.match(r"^\d{2}:\d{2}$", new_start_time):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be HH:MM"}})
    try:
        h, m = map(int, new_start_time.split(":"))
        start_time_min = h * 60 + m
        if start_time_min % 15 != 0:
            raise ValueError()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be a multiple of 15 minutes"}})

    # Validate party_size
    try:
        party_size = int(new_party_size)
        if not (1 <= party_size <= 3):
            raise APIError(400, "PARTY_SIZE_EXCEEDED", "Mỗi lượt đặt tối đa 3 người. Nhóm đông hơn vui lòng liên hệ trực tiếp cửa hàng.", {"shop_phone": booking.shop.phone})
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"party_size": "Party size must be an integer"}})

    # CHỈ ĐỊNH của khách (BR-04). PATCH là merge: field không nhắc tới thì giữ
    # nguyên chỉ định cũ — đọc `requested_therapist_id` chứ KHÔNG phải
    # `therapist_id` (người BE đã phân công). Khách không đòi ai mà đọc nhầm cột
    # phân công thì đổi giờ sẽ bị ghim vào đúng người cũ và ăn THERAPIST_OFF_SHIFT
    # oan (BR-21, US-02 AC2).
    first_res = booking.reservations[0] if booking.reservations else None

    if "therapist_id" in data:
        therapist_id = new_therapist_id
    elif party_size == 1 and first_res:
        therapist_id = first_res.requested_therapist_id
    else:
        # Lên nhóm ≥2 thì chỉ định cũ hết hiệu lực, bỏ im lặng thay vì báo lỗi.
        therapist_id = None

    if "therapist_gender" in data:
        therapist_gender_str = new_therapist_gender_str
    elif party_size == 1 and first_res and not therapist_id:
        therapist_gender_str = (
            first_res.therapist_gender.value if first_res.therapist_gender else None
        )
    else:
        therapist_gender_str = None

    therapist_gender = None
    if therapist_gender_str:
        if therapist_gender_str not in ["male", "female"]:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_gender": "Gender must be male or female"}})
        therapist_gender = Gender(therapist_gender_str)

    if party_size >= 2 and (therapist_id or therapist_gender):
        raise APIError(400, "THERAPIST_NOT_ALLOWED", "Đặt cho nhóm từ 2 người trở lên không thể chỉ định nhân viên.")
    if therapist_id and therapist_gender:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, không thể chọn cả nhân viên và giới tính.")

    # Handle reservations
    if new_reservations is not None:
        if not isinstance(new_reservations, list):
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"reservations": "Must be a list"}})
        if len(new_reservations) != party_size:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, số lượng reservations không khớp party_size.", {"fields": {"reservations": "Mismatch with party_size"}})
    else:
        # Keep existing addons per guest
        new_reservations = []
        for r in booking.reservations:
            new_reservations.append({"addon_ids": [a.id for a in r.addons]})

    # Collect addon ids
    res_addon_ids = []
    all_addon_ids = []
    for j, r in enumerate(new_reservations):
        addon_ids = r.get("addon_ids", [])
        if not isinstance(addon_ids, list):
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, addon_ids phải là danh sách số nguyên.", {"fields": {f"reservations[{j}].addon_ids": "Must be a list"}})
        try:
            addon_ids = [int(a) for a in addon_ids]
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, addon_ids phải là danh sách số nguyên.", {"fields": {f"reservations[{j}].addon_ids": "Must be integers"}})
        res_addon_ids.append(addon_ids)
        all_addon_ids.extend(addon_ids)

    # Validate course
    if new_course_id is not None:
        try:
            new_course_id = int(new_course_id)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"course_id": "Course ID must be an integer"}})
        course = Course.query.filter_by(id=new_course_id, shop_id=booking.shop_id, is_active=True).first()
        if not course:
            raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ chính.")
    else:
        course = booking.reservations[0].main_course
        new_course_id = course.id

    # Validate addons
    valid_addons = []
    if all_addon_ids:
        valid_addons = Addon.query.filter(Addon.id.in_(all_addon_ids), Addon.shop_id == booking.shop_id, Addon.is_active == True).all()
        if len(valid_addons) != len(set(all_addon_ids)):
            raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ bổ sung.")

    # Check combo restrictions
    if all_addon_ids:
        restrictions = db.session.query(combo_restriction).filter(
            combo_restriction.c.course_id == new_course_id,
            combo_restriction.c.addon_id.in_(all_addon_ids)
        ).all()
        if restrictions:
            restricted_addon_id = restrictions[0].addon_id
            restricted_addon = next(a for a in valid_addons if a.id == restricted_addon_id)
            raise APIError(
                422,
                "INVALID_COMBO",
                f"Dịch vụ {restricted_addon.name} không thể đặt kèm {course.name}. Vui lòng chọn tổ hợp khác.",
                {"course_id": new_course_id, "addon_id": restricted_addon_id}
            )

    # Check therapist off shift if specified
    if therapist_id:
        therapist = Therapist.query.filter_by(id=therapist_id, shop_id=booking.shop_id).first()
        if not therapist:
            raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy nhân viên yêu cầu.")
        total_dur = course.duration_min + sum(a.duration_min for a in valid_addons)
        shifts_t = Shift.query.filter_by(therapist_id=therapist_id, work_date=query_date).all()
        covered = False
        for s in shifts_t:
            s_start = time_to_minutes(s.start_time)
            s_end = time_to_minutes(s.end_time)
            if s_start <= start_time_min and s_end >= start_time_min + total_dur:
                covered = True
                break
        if not covered:
            raise APIError(
                422,
                "THERAPIST_OFF_SHIFT",
                f"Nhân viên {therapist.name} không làm việc trong khung giờ này. Bạn có thể đổi giờ khác hoặc bỏ chỉ định.",
                {"therapist_id": therapist_id}
            )

    # Deadline check for NEW appointment time
    new_appointment_dt = datetime.combine(query_date, time(start_time_min // 60, start_time_min % 60))
    if new_appointment_dt - datetime.now() < timedelta(hours=1):
        raise APIError(422, "MODIFY_DEADLINE_PASSED", "Đã quá thời hạn thay đổi online (trước giờ hẹn 1 tiếng). Vui lòng gọi trực tiếp cửa hàng.", {"shop_phone": booking.shop.phone})

    # Check shop change
    if "shop_id" in data and int(data["shop_id"]) != booking.shop_id:
        raise APIError(422, "SHOP_CHANGE_NOT_ALLOWED", "Không thể đổi cửa hàng online. Vui lòng liên hệ cửa hàng để được hỗ trợ.", {"shop_phone": booking.shop.phone})

    # Transaction with lock
    res_addon_durs = [sum(Addon.query.get(aid).duration_min for aid in res_addon_ids[idx]) for idx in range(party_size)]

    try:
        # Lock therapists of this shop
        db.session.query(Therapist).filter(Therapist.shop_id == booking.shop_id).with_for_update().all()

        # Prefetch availability excluding THIS booking
        shifts_by_therapist, busy_by_therapist, therapist_map = prefetch_avail_data(booking.shop_id, query_date, exclude_booking_id=booking.id)

        # Check availability
        ok, assigned_therapists = check_availability_at_time(
            shop_id=booking.shop_id,
            query_date=query_date,
            t_min=start_time_min,
            party_size=party_size,
            course_dur=course.duration_min,
            res_addon_durs=res_addon_durs,
            therapist_id=therapist_id,
            therapist_gender=therapist_gender,
            shifts_by_therapist=shifts_by_therapist,
            busy_by_therapist=busy_by_therapist,
            therapist_map=therapist_map
        )

        if not ok:
            suggested = find_suggested_slots(
                shop_id=booking.shop_id,
                query_date=query_date,
                requested_start_min=start_time_min,
                party_size=party_size,
                course_dur=course.duration_min,
                res_addon_durs=res_addon_durs,
                therapist_id=therapist_id,
                therapist_gender=therapist_gender,
                shifts_by_therapist=shifts_by_therapist,
                busy_by_therapist=busy_by_therapist,
                therapist_map=therapist_map
            )
            raise APIError(
                409,
                "SLOT_CONFLICT",
                "Rất tiếc, khung giờ này vừa có người đặt trước. Bạn có thể chọn một trong các giờ gần nhất còn trống.",
                {"suggested_slots": suggested}
            )

        # Update booking
        booking.booking_date = query_date
        booking.start_time = time(start_time_min // 60, start_time_min % 60)
        booking.party_size = party_size
        booking.updated_at = datetime.now()

        # Delete old reservations (cascade will handle reservation_addon)
        for r in booking.reservations:
            db.session.delete(r)
        db.session.flush()

        # Create new reservations
        for j in range(party_size):
            slot_time = time(start_time_min // 60, start_time_min % 60)
            slot = TimeSlot.query.filter_by(shop_id=booking.shop_id, slot_date=query_date, start_time=slot_time).first()
            if not slot:
                slot = TimeSlot(
                    shop_id=booking.shop_id,
                    slot_date=query_date,
                    start_time=slot_time,
                    status=SlotStatus.AVAILABLE
                )
                db.session.add(slot)
                db.session.flush()

            reservation = Reservation(
                booking_id=booking.id,
                guest_no=j + 1,
                main_course_id=new_course_id,
                # Reservation cũ bị xoá và tạo lại, nên phải ghi lại CHỈ ĐỊNH của
                # khách — không mang sang thì lần sửa sau đọc ra None và tưởng
                # khách chưa từng yêu cầu ai (BR-21).
                requested_therapist_id=therapist_id,
                therapist_gender=therapist_gender,
                therapist_id=assigned_therapists[j],  # BE phân công
                slot_id=slot.id
            )
            db.session.add(reservation)
            db.session.flush()

            if res_addon_ids[j]:
                addon_obj_list = Addon.query.filter(Addon.id.in_(res_addon_ids[j])).all()
                reservation.addons.extend(addon_obj_list)
                db.session.flush()

        # Update time_slot status
        for j in range(party_size):
            t_id = assigned_therapists[j]
            r_dur = course.duration_min + res_addon_durs[j]
            busy_by_therapist.setdefault(t_id, []).append((start_time_min, start_time_min + r_dur))

        any_avail = False
        for t_id in shifts_by_therapist:
            shift_covered = False
            for s_start, s_end in shifts_by_therapist[t_id]:
                if s_start <= start_time_min and s_end >= start_time_min + 15:
                    shift_covered = True
                    break
            if not shift_covered:
                continue
            t_busy = False
            for b_start, b_end in busy_by_therapist.get(t_id, []):
                if max(start_time_min, b_start) < min(start_time_min + 15, b_end):
                    t_busy = True
                    break
            if not t_busy:
                any_avail = True
                break

        slot_time = time(start_time_min // 60, start_time_min % 60)
        slot = TimeSlot.query.filter_by(shop_id=booking.shop_id, slot_date=query_date, start_time=slot_time).first()
        if slot:
            slot.status = SlotStatus.AVAILABLE if any_avail else SlotStatus.BOOKED

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        raise e

    send_booking_email(booking.booking_code, booking.customer.email, action="update")

    # Return response without edit_token
    response = format_booking_response(booking).get_json()
    response.pop("edit_token", None)
    response.pop("edit_token_expires_in", None)

    return jsonify(response), 200


# ============================================================
# 2.3 POST /bookings/{bookingCode}/cancel
# ============================================================
@api_bp.route("/bookings/<string:booking_code>/cancel", methods=["POST"])
@rate_limit(10, 60)
def cancel_booking(booking_code):
    data = request.get_json() or {}
    email = data.get("email")

    if not email:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"email": "Missing"}})

    booking_code = booking_code.strip().upper()
    email = email.strip().lower()

    booking = Booking.query.filter_by(booking_code=booking_code).first()
    if not booking:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")
    if booking.customer.email.strip().lower() != email:
        raise APIError(404, "BOOKING_NOT_FOUND", "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email.")

    # Idempotent: already cancelled
    if booking.status == BookingStatus.CANCELLED:
        response = format_booking_response(booking).get_json()
        response.pop("edit_token", None)
        response.pop("edit_token_expires_in", None)
        response["can_modify"] = False
        return jsonify(response), 200

    # Check terminal states
    if booking.status in (BookingStatus.COMPLETED, BookingStatus.NO_SHOW):
        raise APIError(422, "INVALID_STATUS_TRANSITION", "Không thể chuyển trạng thái này.", {"from": booking.status.value, "to": "cancelled"})

    # Deadline check
    if not can_modify_booking(booking):
        raise APIError(422, "MODIFY_DEADLINE_PASSED", "Đã quá thời hạn thay đổi online (trước giờ hẹn 1 tiếng). Vui lòng gọi trực tiếp cửa hàng.", {"shop_phone": booking.shop.phone})

    try:
        booking.status = BookingStatus.CANCELLED
        booking.updated_at = datetime.now()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        raise e

    send_booking_email(booking.booking_code, booking.customer.email, action="cancel")

    response = format_booking_response(booking).get_json()
    response.pop("edit_token", None)
    response.pop("edit_token_expires_in", None)
    response["can_modify"] = False

    return jsonify(response), 200