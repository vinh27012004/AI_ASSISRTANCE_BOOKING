from flask import jsonify, request
from datetime import datetime, date, time
import re

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
    IdempotencyKey,
    combo_restriction,
    Gender,
    MemberType,
    SlotStatus,
    BookingStatus,
)
from app.api import api_bp
from app.api.errors import APIError
from app.api.booking_helpers import (
    time_to_minutes,
    send_booking_email,
    prefetch_avail_data,
    check_availability_at_time,
    find_suggested_slots,
    get_slots_logic,
    generate_booking_code,
    format_booking_response,
    validate_booking_request,
    hash_booking_request,
)


@api_bp.route("/shops", methods=["GET"])
def get_shops():
    shops = Shop.query.all()
    return jsonify([{
        "id": s.id,
        "shop_code": s.shop_code,
        "name": s.name,
        "address": s.address,
        "phone": s.phone
    } for s in shops]), 200


@api_bp.route("/shops/<int:shop_id>/services", methods=["GET"])
def get_services(shop_id):
    shop = Shop.query.get(shop_id)
    if not shop:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    date_str = request.args.get("date")
    party_size_str = request.args.get("party_size")

    if not date_str:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Missing date parameter"}})

    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    if party_size_str:
        try:
            party_size = int(party_size_str)
            if not (1 <= party_size <= 3):
                raise ValueError()
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"party_size": "Party size must be between 1 and 3"}})

    has_shifts = db.session.query(Shift).join(Therapist).filter(
        Therapist.shop_id == shop_id,
        Shift.work_date == query_date
    ).first() is not None

    if not has_shifts:
        return jsonify({
            "courses": [],
            "addons": [],
            "reason": "SHOP_CLOSED"
        }), 200

    courses = Course.query.filter_by(shop_id=shop_id, is_active=True).all()
    addons = Addon.query.filter_by(shop_id=shop_id, is_active=True).all()

    addon_ids = [a.id for a in addons]
    restrictions = db.session.query(combo_restriction).filter(
        combo_restriction.c.addon_id.in_(addon_ids) if addon_ids else False
    ).all()

    restricted_map = {}
    for c_id, a_id in restrictions:
        restricted_map.setdefault(a_id, []).append(c_id)

    return jsonify({
        "courses": [{
            "id": c.id,
            "name": c.name,
            "duration_min": c.duration_min,
            "price": c.price
        } for c in courses],
        "addons": [{
            "id": a.id,
            "name": a.name,
            "duration_min": a.duration_min,
            "price": a.price,
            "restricted_course_ids": restricted_map.get(a.id, [])
        } for a in addons],
        "reason": None
    }), 200


@api_bp.route("/shops/<int:shop_id>/slots", methods=["GET"])
def get_slots(shop_id):
    shop = Shop.query.get(shop_id)
    if not shop:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    date_str = request.args.get("date")
    party_size_str = request.args.get("party_size")
    course_id_str = request.args.get("course_id")
    addon_ids_str = request.args.get("addon_ids")
    therapist_id_str = request.args.get("therapist_id")
    therapist_gender_str = request.args.get("therapist_gender")

    if not date_str or not party_size_str or not course_id_str:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {
            "fields": {
                "date": "Missing" if not date_str else None,
                "party_size": "Missing" if not party_size_str else None,
                "course_id": "Missing" if not course_id_str else None
            }
        })

    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    try:
        party_size = int(party_size_str)
        if not (1 <= party_size <= 3):
            raise APIError(400, "PARTY_SIZE_EXCEEDED", "Mỗi lượt đặt tối đa 3 người. Nhóm đông hơn vui lòng liên hệ trực tiếp cửa hàng.", {"shop_phone": shop.phone})
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"party_size": "Party size must be an integer"}})

    try:
        course_id = int(course_id_str)
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"course_id": "Course ID must be an integer"}})

    addon_ids = []
    addon_ids_raw = request.args.get("addon_ids")
    if addon_ids_raw:
        try:
            addon_ids = [int(x) for x in addon_ids_raw.split(",") if x.strip()]
        except ValueError:
            pass
    if not addon_ids:
        addon_ids_list = request.args.getlist("addon_ids")
        if addon_ids_list:
            try:
                addon_ids = [int(x) for x in addon_ids_list]
            except ValueError:
                raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"addon_ids": "Addon IDs must be integers"}})

    therapist_id = None
    if therapist_id_str:
        try:
            therapist_id = int(therapist_id_str)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_id": "Therapist ID must be an integer"}})

    therapist_gender = None
    if therapist_gender_str:
        if therapist_gender_str not in ["male", "female"]:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_gender": "Gender must be male or female"}})
        therapist_gender = Gender(therapist_gender_str)

    if party_size >= 2 and (therapist_id or therapist_gender):
        raise APIError(400, "THERAPIST_NOT_ALLOWED", "Đặt cho nhóm từ 2 người trở lên không thể chỉ định nhân viên.")

    if therapist_id and therapist_gender:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, không thể chọn cả nhân viên và giới tính.")

    slots = get_slots_logic(
        shop_id=shop_id,
        query_date=query_date,
        party_size=party_size,
        course_id=course_id,
        addon_ids=addon_ids,
        therapist_id=therapist_id,
        therapist_gender=therapist_gender
    )

    return jsonify({"slots": slots}), 200


@api_bp.route("/shops/<int:shop_id>/timeline", methods=["GET"])
def get_timeline(shop_id):
    """Lịch trong ngày theo TỪNG nhân viên — nguồn dữ liệu cho timeline ở bước
    Booking (wireframe 02): hàng nào trắng là trống, xanh là đã đặt, gạch chéo
    là ngoài ca.

    Public nhưng chỉ lộ những gì khách đứng ở quầy vốn nhìn thấy: tên nhân viên,
    ca làm, và khoảng giờ đã kín kèm tên course. KHÔNG bao giờ trả thông tin
    khách đặt (tên/SĐT/email) — timeline chỉ nói "giờ này ai bận", không nói
    "ai đặt".
    """
    shop = Shop.query.get(shop_id)
    if not shop:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    date_str = request.args.get("date")
    if not date_str:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Missing date parameter"}})
    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    # Chỉ nhân viên CÓ CA ngày đó mới thành một hàng — người nghỉ không có gì để vẽ.
    shifts = (
        Shift.query.join(Therapist)
        .filter(Therapist.shop_id == shop_id, Shift.work_date == query_date)
        .order_by(Shift.start_time)
        .all()
    )
    shifts_by_therapist = {}
    for s in shifts:
        shifts_by_therapist.setdefault(s.therapist_id, []).append(s)

    therapists = (
        Therapist.query.filter(Therapist.id.in_(shifts_by_therapist.keys()))
        .order_by(Therapist.name)
        .all()
        if shifts_by_therapist
        else []
    )

    # Khoảng đã đặt: theo therapist ĐƯỢC PHÂN CÔNG (BR-21) — cancelled không chiếm chỗ.
    reservations = (
        db.session.query(Reservation)
        .join(Booking)
        .filter(
            Booking.shop_id == shop_id,
            Booking.booking_date == query_date,
            Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING]),
            Reservation.therapist_id.isnot(None),
        )
        .all()
    )
    bookings_by_therapist = {}
    for r in reservations:
        start_min = time_to_minutes(r.booking.start_time)
        dur = r.main_course.duration_min + sum(a.duration_min for a in r.addons)
        bookings_by_therapist.setdefault(r.therapist_id, []).append({
            "start_time": f"{start_min // 60:02d}:{start_min % 60:02d}",
            "end_time": f"{(start_min + dur) // 60:02d}:{(start_min + dur) % 60:02d}",
            "course_name": r.main_course.name,
        })

    return jsonify({
        "date": date_str,
        "therapists": [{
            "id": t.id,
            "name": t.name,
            "gender": t.gender.value,
            "shifts": [{
                "start_time": s.start_time.strftime("%H:%M"),
                "end_time": s.end_time.strftime("%H:%M"),
            } for s in shifts_by_therapist[t.id]],
            "bookings": sorted(
                bookings_by_therapist.get(t.id, []),
                key=lambda b: b["start_time"],
            ),
        } for t in therapists],
    }), 200


@api_bp.route("/shops/<int:shop_id>/therapists", methods=["GET"])
def get_therapists(shop_id):
    """1.6 — danh sách nhân viên cho bước chỉ định đích danh (UC-07, US-02 AC1).

    Public, nhưng chỉ trả thông tin khách vốn nhìn thấy ở cửa hàng: tên + giới tính.
    Không SĐT, không tài khoản, không lịch ca.
    """
    shop = Shop.query.get(shop_id)
    if not shop:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    query = Therapist.query.filter_by(shop_id=shop_id)

    date_str = request.args.get("date")
    if date_str:
        try:
            query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})
        # Lọc còn người CÓ CA ngày đó -> khách không chọn phải người chắc chắn nghỉ,
        # đỡ một vòng 422 THERAPIST_OFF_SHIFT (case A4). Có ca != còn rảnh: chốt chặn
        # thật vẫn là GET /slots?therapist_id= và transaction trong POST /bookings.
        query = query.join(Shift).filter(Shift.work_date == query_date).distinct()

    therapists = query.order_by(Therapist.name).all()

    return jsonify({
        "therapists": [{
            "id": t.id,
            "name": t.name,
            "gender": t.gender.value
        } for t in therapists]
    }), 200


@api_bp.route("/customers/lookup", methods=["POST"])
def lookup_customer():
    data = request.get_json() or {}
    phone = data.get("phone")
    if not phone:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"phone": "Missing phone parameter"}})

    if not re.match(r"^\d{8,15}$", phone):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"phone": "Invalid phone format"}})

    ng_entry = NgList.query.filter_by(phone=phone).first()
    if ng_entry:
        shop = Shop.query.first()
        shop_phone = shop.phone if shop else "090-0000-0000"
        raise APIError(403, "PHONE_BLOCKED", "Số điện thoại này hiện không thể đặt chỗ online. Vui lòng liên hệ trực tiếp cửa hàng để được hỗ trợ.", {
            "reason": ng_entry.reason or "Lý do chặn không được cung cấp.",
            "shop_phone": shop_phone
        })

    customer = Customer.query.filter_by(phone=phone).first()
    if customer:
        return jsonify({
            "member_type": customer.member_type.value,
            "rank": customer.rank,
            "visit_count": customer.visit_count
        }), 200
    else:
        return jsonify({
            "member_type": "guest",
            "rank": None,
            "visit_count": 0
        }), 200


@api_bp.route("/bookings", methods=["POST"])
def create_booking():
    data = request.get_json() or {}

    # Bước 0 — Idempotency-Key (GĐ2, api-design §7.1). Chatbot đặt key = conversation_id.
    # Pre-check rẻ: key đã map booking rồi thì trả lại luôn, khỏi validate + transaction.
    # (Chốt chặn race song song nằm trong transaction bên dưới — sau khi giữ lock.)
    idem_key = request.headers.get("Idempotency-Key")
    idem_hash = None
    if idem_key:
        idem_hash = hash_booking_request(data)
        existing = IdempotencyKey.query.filter_by(idem_key=idem_key).first()
        if existing:
            if existing.request_hash == idem_hash:
                # Gửi lại y hệt → trả booking đã tạo, cấp edit_token mới (không tạo mới).
                return format_booking_response(existing.booking, include_edit_token=True), 200
            # Cùng key nhưng payload khác → tái dùng nhầm, chặn để không map lẫn đơn.
            raise APIError(
                422, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.",
                {"fields": {"Idempotency-Key": "key đã dùng cho đơn khác"}},
            )

    # Bước 1–7 (format, BR-14, BR-04, course/addon, BR-09, BR-06, BR-05) dùng chung
    # với PATCH /bookings/{code} để hai đường không bao giờ lệch luật — xem mục 2.2.
    v = validate_booking_request(data, check_ng_list=True)

    shop = v.shop
    shop_id = shop.id
    query_date = v.query_date
    start_time_min = v.start_time_min
    party_size = v.party_size
    phone = v.phone
    email = v.email
    course = v.course
    course_id = course.id
    res_addon_ids = v.res_addon_ids
    res_addon_durs = v.res_addon_durs
    therapist_id = v.therapist_id
    therapist_gender = v.therapist_gender


    # Dedup theo thời gian 120s — lớp CŨ, chỉ chạy khi KHÔNG có Idempotency-Key (fallback
    # cho FE web, §7.1). Có key thì đã xử lý chính xác hơn ở bước 0 nên bỏ lớp này để
    # không gộp nhầm "đổi course/add-on cùng giờ trong 120s" về đơn đầu.
    if not idem_key:
        customer_temp = Customer.query.filter_by(phone=phone).first()
        if customer_temp:
            recent_booking = Booking.query.filter_by(
                shop_id=shop_id,
                customer_id=customer_temp.id,
                booking_date=query_date,
                start_time=time(start_time_min // 60, start_time_min % 60)
            ).order_by(Booking.created_at.desc()).first()

            if recent_booking and (datetime.now() - recent_booking.created_at).total_seconds() < 120:
                return format_booking_response(recent_booking, include_edit_token=True)

    # 8. Transaction + lock slot/therapist
    try:
        # Lock therapists of this shop to prevent race conditions
        db.session.query(Therapist).filter(Therapist.shop_id == shop_id).with_for_update().all()

        # Re-check Idempotency-Key TRONG transaction, sau khi đã giữ lock (lock này
        # serialize mọi POST /bookings cùng shop → thấy được bản ghi mà request song
        # song vừa commit). Chống 2 request cùng key chạy chồng nhau tạo 2 booking.
        if idem_key:
            existing = IdempotencyKey.query.filter_by(idem_key=idem_key).first()
            if existing:
                db.session.rollback()
                if existing.request_hash == idem_hash:
                    return format_booking_response(existing.booking, include_edit_token=True), 200
                raise APIError(
                    422, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.",
                    {"fields": {"Idempotency-Key": "key đã dùng cho đơn khác"}},
                )

        shifts_by_therapist, busy_by_therapist, therapist_map = prefetch_avail_data(shop_id, query_date)

        # Check availability
        ok, assigned_therapists = check_availability_at_time(
            shop_id=shop_id,
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
                shop_id=shop_id,
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

        # Upsert customer
        customer = Customer.query.filter_by(phone=phone).first()
        if not customer:
            customer = Customer(
                phone=phone,
                email=email,
                member_type=MemberType.GUEST,
                visit_count=0
            )
            db.session.add(customer)
            db.session.flush()
        else:
            customer.email = email

        # Create booking
        booking_code = generate_booking_code(shop.shop_code, query_date)
        booking = Booking(
            booking_code=booking_code,
            shop_id=shop_id,
            customer_id=customer.id,
            booking_date=query_date,
            start_time=time(start_time_min // 60, start_time_min % 60),
            party_size=party_size,
            status=BookingStatus.CONFIRMED
        )
        db.session.add(booking)
        db.session.flush()

        # Create reservations
        for j in range(party_size):
            slot_time = time(start_time_min // 60, start_time_min % 60)
            slot = TimeSlot.query.filter_by(shop_id=shop_id, slot_date=query_date, start_time=slot_time).first()
            if not slot:
                slot = TimeSlot(
                    shop_id=shop_id,
                    slot_date=query_date,
                    start_time=slot_time,
                    status=SlotStatus.AVAILABLE
                )
                db.session.add(slot)
                db.session.flush()

            # BR-21: lưu TÁCH BẠCH "khách yêu cầu gì" và "BE phân ai".
            # Nhóm >=2 đã bị chặn chỉ định ở bước 3 nên therapist_id/therapist_gender
            # chắc chắn None ở đây — requested_* để None là đúng (BR-04).
            reservation = Reservation(
                booking_id=booking.id,
                guest_no=j + 1,
                main_course_id=course_id,
                requested_therapist_id=therapist_id,      # khách chỉ định đích danh
                therapist_gender=therapist_gender,        # khách chỉ định theo giới tính
                therapist_id=assigned_therapists[j],      # BE phân công
                slot_id=slot.id
            )
            db.session.add(reservation)
            db.session.flush()

            if res_addon_ids[j]:
                addon_obj_list = Addon.query.filter(Addon.id.in_(res_addon_ids[j])).all()
                reservation.addons.extend(addon_obj_list)
                db.session.flush()

        # Update Slot Status:
        # Check if there's at least one therapist available for a 15-minute slot starting at start_time_min
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
        slot = TimeSlot.query.filter_by(shop_id=shop_id, slot_date=query_date, start_time=slot_time).first()
        if slot:
            slot.status = SlotStatus.AVAILABLE if any_avail else SlotStatus.BOOKED

        # GĐ2 (§7.1): map idem_key -> booking TRONG cùng transaction. Retry cùng key sau
        # này trả đúng booking này; UNIQUE(idem_key) là chốt chặn cuối nếu lọt race.
        if idem_key:
            db.session.add(IdempotencyKey(
                idem_key=idem_key,
                booking_id=booking.id,
                request_hash=idem_hash,
            ))

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        raise e

    # 9. Send email SES
    send_booking_email(booking.booking_code, email, action="create")

    # Response 201
    return format_booking_response(booking, include_edit_token=True), 201