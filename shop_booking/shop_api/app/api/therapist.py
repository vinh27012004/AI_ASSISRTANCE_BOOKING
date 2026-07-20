from flask import jsonify, request
from datetime import datetime, date, time, timedelta, timezone

from app.extensions import db
from app.models.shop import (
    Shop,
    Course,
    Addon,
    Therapist,
    Shift,
    Customer,
    Booking,
    Reservation,
    BookingStatus,
)
from app.api import api_bp
from app.api.errors import APIError
from app.api.booking_helpers import time_to_minutes
from app.api.auth_admin import require_role


# ============================================================
# 4.1 GET /therapists/me/schedule
# ============================================================
@api_bp.route("/therapists/me/schedule", methods=["GET"])
@require_role("therapist")
def therapist_my_schedule():
    # therapist_id comes from token (attached by require_role decorator)
    therapist_id = request.auth_payload.get("therapist_id")
    if not therapist_id:
        raise APIError(403, "FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.")

    date_str = request.args.get("date")
    if not date_str:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Missing date parameter"}})

    try:
        query_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})

    # Fetch shifts for this therapist on this date
    shifts = Shift.query.filter_by(
        therapist_id=therapist_id,
        work_date=query_date
    ).order_by(Shift.start_time).all()

    # Fetch bookings assigned to this therapist on this date
    # Only confirmed/pending/completed (not cancelled/no_show)
    reservations = db.session.query(Reservation).join(Booking).filter(
        Reservation.therapist_id == therapist_id,
        Booking.booking_date == query_date,
        Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING, BookingStatus.COMPLETED])
    ).order_by(Booking.start_time).all()

    # Helper to mask phone
    def mask_phone(phone: str) -> str:
        if len(phone) >= 7:
            return phone[:3] + "****" + phone[-4:]
        return phone

    # Build response
    shifts_data = [{
        "start_time": s.start_time.strftime("%H:%M"),
        "end_time": s.end_time.strftime("%H:%M"),
    } for s in shifts]

    bookings_data = []
    for r in reservations:
        b = r.booking
        course = r.main_course
        duration_min = course.duration_min + sum(a.duration_min for a in r.addons)
        bookings_data.append({
            "start_time": b.start_time.strftime("%H:%M"),
            "duration_min": duration_min,
            "course_name": course.name,
            "addon_names": [a.name for a in r.addons],
            "guest_no": r.guest_no,
            "party_size": b.party_size,
            "customer_phone_masked": mask_phone(b.customer.phone),
        })

    return jsonify({
        "date": query_date.strftime("%Y-%m-%d"),
        "shifts": shifts_data,
        "bookings": bookings_data,
    }), 200