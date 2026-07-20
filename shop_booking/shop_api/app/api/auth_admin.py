from flask import jsonify, request
from datetime import datetime, date, time, timedelta, timezone
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
    Account,
    Booking,
    combo_restriction,
    Gender,
    Role,
    BookingStatus,
)
from app.api import api_bp
from app.api.errors import APIError
from app.api.rate_limit import rate_limit
from app.api.booking_helpers import (
    time_to_minutes,
    can_modify_booking,
    _secret_key,
    INVALID_TOKEN_ERRORS,
)
from werkzeug.security import generate_password_hash, check_password_hash
import jwt


# ============================================================
# Auth helpers
# ============================================================
# Băm sẵn lúc import để mọi lần login sai username đều tốn đúng một lần
# check_password_hash — xem ghi chú ở view `login`.
_DUMMY_PASSWORD_HASH = generate_password_hash("timing-equalizer-not-a-real-password")


def make_access_token(account: Account) -> str:
    """JWT access token for admin/therapist (8h TTL, typ='access')."""
    payload = {
        "typ": "access",
        # PyJWT >= 2.10 bắt `sub` phải là string: encode int vẫn lọt nhưng decode
        # ném InvalidSubjectError -> mọi endpoint admin/therapist trả 401.
        "sub": str(account.id),
        "role": account.role.value,
        "therapist_id": account.therapist_id,
        "exp": datetime.now(timezone.utc) + timedelta(hours=8),
    }
    return jwt.encode(payload, _secret_key(), algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode access token. Returns payload on success, raises APIError on failure."""
    try:
        payload = jwt.decode(token, _secret_key(), algorithms=["HS256"])
    except INVALID_TOKEN_ERRORS:
        raise APIError(401, "UNAUTHORIZED", "Thông tin đăng nhập không đúng.")
    if payload.get("typ") != "access":
        raise APIError(401, "UNAUTHORIZED", "Thông tin đăng nhập không đúng.")
    return payload


def require_role(*allowed_roles):
    """Decorator: validates Bearer token and role. Raises APIError(401/403)."""
    def decorator(f):
        def wrapper(*args, **kwargs):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                raise APIError(401, "UNAUTHORIZED", "Thông tin đăng nhập không đúng.")
            token = auth_header.split(" ", 1)[1]
            payload = decode_access_token(token)
            role = payload.get("role")
            if role not in allowed_roles:
                raise APIError(403, "FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.")
            if role == "therapist" and payload.get("therapist_id") is None:
                raise APIError(403, "FORBIDDEN", "Bạn không có quyền thực hiện thao tác này.")
            # Attach payload to request for downstream use
            request.auth_payload = payload
            return f(*args, **kwargs)
        wrapper.__name__ = f.__name__
        return wrapper
    return decorator


# ============================================================
# 3.0 POST /auth/login
# ============================================================
@api_bp.route("/auth/login", methods=["POST"])
@rate_limit(5, 60)
def login():
    data = request.get_json() or {}
    username = data.get("username")
    password = data.get("password")
    if not username or not password:
        raise APIError(401, "UNAUTHORIZED", "Thông tin đăng nhập không đúng.")

    account = Account.query.filter_by(username=username).first()

    # Username không tồn tại vẫn phải băm một lần với hash giả rồi mới trả 401.
    # `account and check_password_hash(...)` bị short-circuit nên bỏ hẳn bước băm
    # khi không có account -> trả lời nhanh hơn hẳn, kẻ tấn công đo thời gian là
    # biết username nào có thật.
    password_hash = account.password_hash if account else _DUMMY_PASSWORD_HASH
    password_ok = check_password_hash(password_hash, password)

    if not account or not password_ok:
        raise APIError(401, "UNAUTHORIZED", "Thông tin đăng nhập không đúng.")

    access_token = make_access_token(account)
    return jsonify({
        "access_token": access_token,
        "role": account.role.value,
        "therapist_id": account.therapist_id,
        "username": account.username,
        # Therapist có tên thật thì hiện tên cho dễ nhận; admin không gắn với bản
        # ghi therapist nào nên đành lấy username.
        "display_name": account.therapist.name if account.therapist else account.username,
        "expires_in": 28800,
    }), 200


# ============================================================
# Admin helpers
# ============================================================
def admin_required(f):
    return require_role("admin")(f)


def shop_admin_required(f):
    """Admin with shop_id parameter. Validates shop_id belongs to admin's scope (all shops for now)."""
    return require_role("admin")(f)


# ============================================================
# 3.1 /admin/courses  and  /admin/addons
# ============================================================
@api_bp.route("/admin/courses", methods=["GET"])
@admin_required
def admin_list_courses():
    shop_id = request.args.get("shop_id", type=int)
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"
    if not shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Missing shop_id parameter"}})

    q = Course.query.filter_by(shop_id=shop_id)
    if not include_inactive:
        q = q.filter_by(is_active=True)
    courses = q.order_by(Course.name).all()
    return jsonify([{
        "id": c.id,
        "name": c.name,
        "duration_min": c.duration_min,
        "price": c.price,
        "is_active": c.is_active,
        "shop_id": c.shop_id,
    } for c in courses]), 200


@api_bp.route("/admin/courses", methods=["POST"])
@admin_required
def admin_create_course():
    data = request.get_json() or {}
    required = ["shop_id", "name", "duration_min", "price"]
    missing = [f for f in required if f not in data]
    if missing:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {f: "Missing parameter" for f in missing}})

    shop_id = data["shop_id"]
    if not Shop.query.get(shop_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy cửa hàng.")

    duration_min = data["duration_min"]
    if not isinstance(duration_min, int) or duration_min <= 0 or duration_min % 15 != 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"duration_min": "Thời lượng phải là bội số của 15 phút."}})

    price = data["price"]
    if not isinstance(price, int) or price < 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"price": "Giá phải là số nguyên không âm."}})

    course = Course(
        shop_id=shop_id,
        name=data["name"],
        duration_min=duration_min,
        price=price,
        is_active=data.get("is_active", True),
    )
    db.session.add(course)
    db.session.commit()
    return jsonify({
        "id": course.id,
        "name": course.name,
        "duration_min": course.duration_min,
        "price": course.price,
        "is_active": course.is_active,
        "shop_id": course.shop_id,
    }), 201


@api_bp.route("/admin/courses/<int:id>", methods=["PATCH"])
@admin_required
def admin_update_course(id):
    course = Course.query.get(id)
    if not course:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    data = request.get_json() or {}
    if "name" in data:
        course.name = data["name"]
    if "duration_min" in data:
        duration_min = data["duration_min"]
        if not isinstance(duration_min, int) or duration_min <= 0 or duration_min % 15 != 0:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"duration_min": "Thời lượng phải là bội số của 15 phút."}})
        course.duration_min = duration_min
    if "price" in data:
        price = data["price"]
        if not isinstance(price, int) or price < 0:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"price": "Giá phải là số nguyên không âm."}})
        course.price = price
    if "is_active" in data:
        course.is_active = bool(data["is_active"])

    db.session.commit()
    return jsonify({
        "id": course.id,
        "name": course.name,
        "duration_min": course.duration_min,
        "price": course.price,
        "is_active": course.is_active,
        "shop_id": course.shop_id,
    }), 200


@api_bp.route("/admin/courses/<int:id>", methods=["DELETE"])
@admin_required
def admin_delete_course(id):
    course = Course.query.get(id)
    if not course:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    # Check if in use
    from app.models.shop import Reservation
    in_use = db.session.query(Reservation).filter_by(main_course_id=id).first()
    if in_use:
        raise APIError(409, "RESOURCE_IN_USE", "Không thể xóa vì dữ liệu đang được sử dụng. Hãy tắt hiển thị (is_active = false) thay vì xóa.", {"used_by": "reservation", "count": db.session.query(Reservation).filter_by(main_course_id=id).count()})

    db.session.delete(course)
    db.session.commit()
    return "", 204


# --- Addons (same pattern) ---
@api_bp.route("/admin/addons", methods=["GET"])
@admin_required
def admin_list_addons():
    shop_id = request.args.get("shop_id", type=int)
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"
    if not shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Missing shop_id parameter"}})

    q = Addon.query.filter_by(shop_id=shop_id)
    if not include_inactive:
        q = q.filter_by(is_active=True)
    addons = q.order_by(Addon.name).all()
    return jsonify([{
        "id": a.id,
        "name": a.name,
        "duration_min": a.duration_min,
        "price": a.price,
        "is_active": a.is_active,
        "shop_id": a.shop_id,
    } for a in addons]), 200


@api_bp.route("/admin/addons", methods=["POST"])
@admin_required
def admin_create_addon():
    data = request.get_json() or {}
    required = ["shop_id", "name", "duration_min", "price"]
    missing = [f for f in required if f not in data]
    if missing:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {f: "Missing parameter" for f in missing}})

    shop_id = data["shop_id"]
    if not Shop.query.get(shop_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy cửa hàng.")

    duration_min = data["duration_min"]
    if not isinstance(duration_min, int) or duration_min <= 0 or duration_min % 15 != 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"duration_min": "Thời lượng phải là bội số của 15 phút."}})

    price = data["price"]
    if not isinstance(price, int) or price < 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"price": "Giá phải là số nguyên không âm."}})

    addon = Addon(
        shop_id=shop_id,
        name=data["name"],
        duration_min=duration_min,
        price=price,
        is_active=data.get("is_active", True),
    )
    db.session.add(addon)
    db.session.commit()
    return jsonify({
        "id": addon.id,
        "name": addon.name,
        "duration_min": addon.duration_min,
        "price": addon.price,
        "is_active": addon.is_active,
        "shop_id": addon.shop_id,
    }), 201


@api_bp.route("/admin/addons/<int:id>", methods=["PATCH"])
@admin_required
def admin_update_addon(id):
    addon = Addon.query.get(id)
    if not addon:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    data = request.get_json() or {}
    if "name" in data:
        addon.name = data["name"]
    if "duration_min" in data:
        duration_min = data["duration_min"]
        if not isinstance(duration_min, int) or duration_min <= 0 or duration_min % 15 != 0:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"duration_min": "Thời lượng phải là bội số của 15 phút."}})
        addon.duration_min = duration_min
    if "price" in data:
        price = data["price"]
        if not isinstance(price, int) or price < 0:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"price": "Giá phải là số nguyên không âm."}})
        addon.price = price
    if "is_active" in data:
        addon.is_active = bool(data["is_active"])

    db.session.commit()
    return jsonify({
        "id": addon.id,
        "name": addon.name,
        "duration_min": addon.duration_min,
        "price": addon.price,
        "is_active": addon.is_active,
        "shop_id": addon.shop_id,
    }), 200


@api_bp.route("/admin/addons/<int:id>", methods=["DELETE"])
@admin_required
def admin_delete_addon(id):
    addon = Addon.query.get(id)
    if not addon:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    from app.models.shop import Reservation
    in_use = db.session.query(Reservation).join(Reservation.addons).filter_by(id=id).first()
    if in_use:
        raise APIError(409, "RESOURCE_IN_USE", "Không thể xóa vì dữ liệu đang được sử dụng. Hãy tắt hiển thị (is_active = false) thay vì xóa.", {"used_by": "reservation", "count": db.session.query(Reservation).join(Reservation.addons).filter_by(id=id).count()})

    db.session.delete(addon)
    db.session.commit()
    return "", 204


# ============================================================
# 3.2 /admin/combo-restrictions
# ============================================================
@api_bp.route("/admin/combo-restrictions", methods=["GET"])
@admin_required
def admin_list_combo_restrictions():
    shop_id = request.args.get("shop_id", type=int)
    if not shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Missing shop_id parameter"}})

    # Join to get names
    restrictions = db.session.query(combo_restriction).join(Course, combo_restriction.c.course_id == Course.id).join(Addon, combo_restriction.c.addon_id == Addon.id).filter(Course.shop_id == shop_id, Addon.shop_id == shop_id).all()

    return jsonify([{
        "course_id": c_id,
        "course_name": Course.query.get(c_id).name if Course.query.get(c_id) else None,
        "addon_id": a_id,
        "addon_name": Addon.query.get(a_id).name if Addon.query.get(a_id) else None,
    } for c_id, a_id in restrictions]), 200


@api_bp.route("/admin/combo-restrictions", methods=["POST"])
@admin_required
def admin_create_combo_restriction():
    data = request.get_json() or {}
    if "course_id" not in data or "addon_id" not in data:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"course_id": "Missing" if "course_id" not in data else None, "addon_id": "Missing" if "addon_id" not in data else None}})

    course_id = data["course_id"]
    addon_id = data["addon_id"]

    course = Course.query.get(course_id)
    addon = Addon.query.get(addon_id)
    if not course or not addon:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dịch vụ.")
    if course.shop_id != addon.shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Course and addon must belong to the same shop"}})

    # Check if already exists
    existing = db.session.query(combo_restriction).filter_by(course_id=course_id, addon_id=addon_id).first()
    if existing:
        raise APIError(409, "COMBO_RESTRICTION_EXISTS", "Cặp dịch vụ này đã có trong danh sách cấm.")

    db.session.execute(combo_restriction.insert().values(course_id=course_id, addon_id=addon_id))
    db.session.commit()
    return jsonify({"course_id": course_id, "addon_id": addon_id}), 201


@api_bp.route("/admin/combo-restrictions", methods=["DELETE"])
@admin_required
def admin_delete_combo_restriction():
    course_id = request.args.get("course_id", type=int)
    addon_id = request.args.get("addon_id", type=int)
    if course_id is None or addon_id is None:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"course_id": "Missing" if course_id is None else None, "addon_id": "Missing" if addon_id is None else None}})

    result = db.session.execute(combo_restriction.delete().where(combo_restriction.c.course_id == course_id, combo_restriction.c.addon_id == addon_id))
    if result.rowcount == 0:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    db.session.commit()
    return "", 204


# ============================================================
# 3.3 /admin/therapists
# ============================================================
@api_bp.route("/admin/therapists", methods=["GET"])
@admin_required
def admin_list_therapists():
    shop_id = request.args.get("shop_id", type=int)
    if not shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Missing shop_id parameter"}})

    therapists = Therapist.query.filter_by(shop_id=shop_id).order_by(Therapist.name).all()
    return jsonify([{
        "id": t.id,
        "name": t.name,
        "gender": t.gender.value,
        "has_account": t.account is not None,
    } for t in therapists]), 200


@api_bp.route("/admin/therapists", methods=["POST"])
@admin_required
def admin_create_therapist():
    data = request.get_json() or {}
    required = ["shop_id", "name", "gender"]
    missing = [f for f in required if f not in data]
    if missing:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {f: "Missing parameter" for f in missing}})

    shop_id = data["shop_id"]
    if not Shop.query.get(shop_id):
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy cửa hàng.")

    gender = data["gender"]
    if gender not in ["male", "female"]:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"gender": "Gender must be male or female"}})

    # Kiểm account TRƯỚC khi insert therapist: 409 sau khi đã flush() thì therapist
    # chỉ còn trông vào teardown rollback, hụt một nhịp là còn lại người mồ côi
    # không tài khoản.
    account_data = data.get("account")
    username = password = None
    if account_data:
        username = (account_data.get("username") or "").strip()
        password = account_data.get("password") or ""
        if not username or not password:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"account": "username and password required"}})
        if Account.query.filter_by(username=username).first():
            raise APIError(409, "USERNAME_TAKEN", "Tên đăng nhập đã tồn tại.")

    therapist = Therapist(
        shop_id=shop_id,
        name=data["name"],
        gender=Gender(gender),
    )
    db.session.add(therapist)
    db.session.flush()  # lấy therapist.id cho account bên dưới

    if account_data:
        db.session.add(Account(
            username=username,
            password_hash=generate_password_hash(password),
            role=Role.THERAPIST,
            therapist_id=therapist.id,
        ))

    # Cả hai chung một transaction (spec 3.3).
    db.session.commit()
    return jsonify({
        "id": therapist.id,
        "name": therapist.name,
        "gender": therapist.gender.value,
        "has_account": therapist.account is not None,
    }), 201


@api_bp.route("/admin/therapists/<int:id>", methods=["PATCH"])
@admin_required
def admin_update_therapist(id):
    therapist = Therapist.query.get(id)
    if not therapist:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    data = request.get_json() or {}

    # --- Validate hết trước, đụng DB sau: raise giữa chừng mà đã sửa dở thì
    # phải trông chờ teardown rollback hộ, dễ sinh dữ liệu nửa vời.
    if "gender" in data and data["gender"] not in ["male", "female"]:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"gender": "Gender must be male or female"}})
    if "shop_id" in data and int(data["shop_id"]) != therapist.shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Cannot change shop. Hire at new shop instead."}})

    # Hai thao tác tài khoản, hai field riêng biệt:
    #   account        = CẤP MỚI cho người chưa có
    #   reset_password = ĐẶT LẠI mật khẩu cho người đã có
    # Tách ra thay vì nhét chung `account` để không bao giờ có chuyện định cấp mới
    # mà lỡ tay ghi đè mật khẩu người đang dùng, hay ngược lại.
    account_data = data.get("account")
    new_password = data.get("reset_password")

    if account_data and new_password:
        raise APIError(400, "VALIDATION_ERROR", "Không thể vừa cấp tài khoản mới vừa đặt lại mật khẩu trong cùng một yêu cầu.")

    if account_data:
        username = (account_data.get("username") or "").strip()
        password = account_data.get("password") or ""
        if not username or not password:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"account": "username and password required"}})
        if therapist.account:
            raise APIError(409, "ACCOUNT_EXISTS", f"Nhân viên {therapist.name} đã có tài khoản đăng nhập rồi.")
        if Account.query.filter_by(username=username).first():
            raise APIError(409, "USERNAME_TAKEN", "Tên đăng nhập đã tồn tại.")

    if new_password is not None:
        if not isinstance(new_password, str) or not new_password:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"reset_password": "Password required"}})
        if not therapist.account:
            raise APIError(409, "ACCOUNT_MISSING", f"Nhân viên {therapist.name} chưa có tài khoản. Hãy cấp tài khoản trước.")

    # --- Từ đây trở xuống mới ghi.
    if "name" in data:
        therapist.name = data["name"]
    if "gender" in data:
        therapist.gender = Gender(data["gender"])
    if account_data:
        db.session.add(Account(
            username=username,
            password_hash=generate_password_hash(password),
            role=Role.THERAPIST,
            therapist_id=therapist.id,
        ))
    if new_password is not None:
        # Lưu ý: access_token đã phát vẫn sống tới hết 8h TTL — JWT không có trạng
        # thái nên đổi mật khẩu KHÔNG đá được phiên đang đăng nhập ra.
        therapist.account.password_hash = generate_password_hash(new_password)

    db.session.commit()
    return jsonify({
        "id": therapist.id,
        "name": therapist.name,
        "gender": therapist.gender.value,
        "has_account": therapist.account is not None,
    }), 200


@api_bp.route("/admin/therapists/<int:id>", methods=["DELETE"])
@admin_required
def admin_delete_therapist(id):
    therapist = Therapist.query.get(id)
    if not therapist:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    # Check if in use
    from app.models.shop import Shift, Reservation
    has_shift = Shift.query.filter_by(therapist_id=id).first()
    has_reservation = Reservation.query.filter_by(therapist_id=id).first()
    if has_shift or has_reservation:
        raise APIError(409, "RESOURCE_IN_USE", "Không thể xóa vì dữ liệu đang được sử dụng. Hãy xóa ca tương lai hoặc giữ lịch sử booking.", {"used_by": "shift" if has_shift else "reservation", "count": (Shift.query.filter_by(therapist_id=id).count() + Reservation.query.filter_by(therapist_id=id).count())})

    # Delete account if exists
    if therapist.account:
        db.session.delete(therapist.account)
    db.session.delete(therapist)
    db.session.commit()
    return "", 204


# ============================================================
# 3.4 /admin/shifts
# ============================================================
@api_bp.route("/admin/shifts", methods=["GET"])
@admin_required
def admin_list_shifts():
    shop_id = request.args.get("shop_id", type=int)
    date_str = request.args.get("date")
    therapist_id = request.args.get("therapist_id", type=int)
    from_str = request.args.get("from")
    to_str = request.args.get("to")

    if not shop_id and not therapist_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Required if no therapist_id", "therapist_id": "Required if no shop_id"}})

    query = Shift.query.join(Therapist)
    if shop_id:
        query = query.filter(Therapist.shop_id == shop_id)
    if therapist_id:
        query = query.filter(Shift.therapist_id == therapist_id)
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.filter(Shift.work_date == d)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})
    if from_str:
        try:
            d = datetime.strptime(from_str, "%Y-%m-%d").date()
            query = query.filter(Shift.work_date >= d)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"from": "Date must be in YYYY-MM-DD format"}})
    if to_str:
        try:
            d = datetime.strptime(to_str, "%Y-%m-%d").date()
            query = query.filter(Shift.work_date <= d)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"to": "Date must be in YYYY-MM-DD format"}})

    shifts = query.order_by(Shift.work_date, Shift.start_time).all()
    return jsonify([{
        "id": s.id,
        "therapist_id": s.therapist_id,
        "therapist_name": s.therapist.name,
        "work_date": s.work_date.strftime("%Y-%m-%d"),
        "start_time": s.start_time.strftime("%H:%M"),
        "end_time": s.end_time.strftime("%H:%M"),
    } for s in shifts]), 200


@api_bp.route("/admin/shifts", methods=["POST"])
@admin_required
def admin_create_shift():
    data = request.get_json() or {}
    required = ["therapist_id", "work_date", "start_time", "end_time"]
    missing = [f for f in required if f not in data]
    if missing:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {f: "Missing parameter" for f in missing}})

    therapist_id = data["therapist_id"]
    therapist = Therapist.query.get(therapist_id)
    if not therapist:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy nhân viên.")

    try:
        work_date = datetime.strptime(data["work_date"], "%Y-%m-%d").date()
    except ValueError:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"work_date": "Date must be in YYYY-MM-DD format"}})

    try:
        h, m = map(int, data["start_time"].split(":"))
        start_time = time(h, m)
    except (ValueError, AttributeError):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be HH:MM"}})

    try:
        h, m = map(int, data["end_time"].split(":"))
        end_time = time(h, m)
    except (ValueError, AttributeError):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"end_time": "Must be HH:MM"}})

    if start_time >= end_time:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"end_time": "end_time must be after start_time"}})

    # Validate 15-minute grid
    if time_to_minutes(start_time) % 15 != 0 or time_to_minutes(end_time) % 15 != 0:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"start_time": "Must be multiple of 15 minutes", "end_time": "Must be multiple of 15 minutes"}})

    # Check overlap with existing shifts
    overlapping = Shift.query.filter(
        Shift.therapist_id == therapist_id,
        Shift.work_date == work_date,
        Shift.start_time < end_time,
        Shift.end_time > start_time
    ).first()
    if overlapping:
        raise APIError(409, "SHIFT_OVERLAP", "Nhân viên đã có ca trùng khung giờ này.", {"conflicting_shift_id": overlapping.id})

    shift = Shift(
        therapist_id=therapist_id,
        work_date=work_date,
        start_time=start_time,
        end_time=end_time,
    )
    db.session.add(shift)
    db.session.commit()
    return jsonify({
        "id": shift.id,
        "therapist_id": shift.therapist_id,
        "work_date": shift.work_date.strftime("%Y-%m-%d"),
        "start_time": shift.start_time.strftime("%H:%M"),
        "end_time": shift.end_time.strftime("%H:%M"),
    }), 201


@api_bp.route("/admin/shifts/<int:id>", methods=["DELETE"])
@admin_required
def admin_delete_shift(id):
    shift = Shift.query.get(id)
    if not shift:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    # Check if shift has active bookings using this therapist
    from app.models.shop import Booking, Reservation
    has_booking = db.session.query(Reservation).join(Booking).filter(
        Reservation.therapist_id == shift.therapist_id,
        Booking.booking_date == shift.work_date,
        Booking.start_time < shift.end_time,
        (Booking.start_time + timedelta(minutes=15)) > shift.start_time,  # simplified overlap
        Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING])
    ).first()
    if has_booking:
        raise APIError(409, "RESOURCE_IN_USE", "Không thể xóa ca đang có booking. Hủy booking trước hoặc đổi lịch.", {"conflicting_booking_id": has_booking.booking_id})

    db.session.delete(shift)
    db.session.commit()
    return "", 204


# ============================================================
# 3.5 /admin/ng-list
# ============================================================
@api_bp.route("/admin/ng-list", methods=["GET"])
@admin_required
def admin_list_ng_list():
    ng_entries = NgList.query.order_by(NgList.added_at.desc()).all()
    return jsonify([{
        "id": n.id,
        "phone": n.phone,
        "reason": n.reason,
        "added_at": n.added_at.strftime("%Y-%m-%d %H:%M:%S"),
    } for n in ng_entries]), 200


@api_bp.route("/admin/ng-list", methods=["POST"])
@admin_required
def admin_add_ng_list():
    data = request.get_json() or {}
    if "phone" not in data:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"phone": "Missing"}})

    phone = data["phone"]
    if not isinstance(phone, str) or not re.match(r"^\d{8,15}$", phone):
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"phone": "Invalid phone format"}})

    if NgList.query.filter_by(phone=phone).first():
        raise APIError(409, "NG_PHONE_EXISTS", "Số điện thoại này đã có trong danh sách chặn.")

    ng_entry = NgList(
        phone=phone,
        reason=data.get("reason"),
    )
    db.session.add(ng_entry)
    db.session.commit()
    return jsonify({
        "id": ng_entry.id,
        "phone": ng_entry.phone,
        "reason": ng_entry.reason,
        "added_at": ng_entry.added_at.strftime("%Y-%m-%d %H:%M:%S"),
    }), 201


@api_bp.route("/admin/ng-list/<int:id>", methods=["DELETE"])
@admin_required
def admin_delete_ng_list(id):
    ng_entry = NgList.query.get(id)
    if not ng_entry:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    db.session.delete(ng_entry)
    db.session.commit()
    return "", 204


# ============================================================
# 3.6 /admin/bookings
# ============================================================
@api_bp.route("/admin/bookings", methods=["GET"])
@admin_required
def admin_list_bookings():
    shop_id = request.args.get("shop_id", type=int)
    if not shop_id:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"shop_id": "Missing shop_id parameter"}})

    date_str = request.args.get("date")
    status_str = request.args.get("status")
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 50, type=int)

    query = Booking.query.filter_by(shop_id=shop_id)
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
            query = query.filter_by(booking_date=d)
        except ValueError:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"date": "Date must be in YYYY-MM-DD format"}})
    if status_str:
        if status_str not in [s.value for s in BookingStatus]:
            raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"status": "Invalid status"}})
        query = query.filter_by(status=BookingStatus(status_str))

    total = query.count()
    bookings = query.order_by(Booking.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for b in bookings:
        course = b.reservations[0].main_course if b.reservations else None
        items.append({
            "id": b.id,
            "booking_code": b.booking_code,
            "status": b.status.value,
            "date": b.booking_date.strftime("%Y-%m-%d"),
            "start_time": b.start_time.strftime("%H:%M"),
            "party_size": b.party_size,
            "customer": {
                "phone": b.customer.phone,
                "email": b.customer.email,
                "member_type": b.customer.member_type.value,
                "rank": b.customer.rank,
                "visit_count": b.customer.visit_count,
            },
            "course": {
                "id": course.id,
                "name": course.name,
                "duration_min": course.duration_min,
                "price": course.price,
            } if course else None,
            # `id` + `therapist_id` để admin đổi được người phụ trách (3.7);
            # `requested_therapist_name` cho thấy khách xin ai mà thực tế ai làm.
            "reservations": [{
                "id": r.id,
                "guest_no": r.guest_no,
                # Add-on mỗi khách một khác nên lượt dài ngắn khác nhau — FE cần
                # con số này mới biết ca của ai phủ nổi lượt nào.
                "duration_min": r.main_course.duration_min + sum(
                    a.duration_min for a in r.addons
                ),
                "therapist_id": r.therapist_id,
                "therapist_name": r.therapist.name if r.therapist else None,
                "requested_therapist_name": (
                    r.requested_therapist.name if r.requested_therapist else None
                ),
                "addons": [{"id": a.id, "name": a.name} for a in r.addons],
            } for r in sorted(b.reservations, key=lambda r: r.guest_no)],
        })

    return jsonify({
        "items": items,
        "page": page,
        "per_page": per_page,
        "total": total,
    }), 200


@api_bp.route("/admin/bookings/<int:id>/status", methods=["PATCH"])
@admin_required
def admin_update_booking_status(id):
    booking = Booking.query.get(id)
    if not booking:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    data = request.get_json() or {}
    if "status" not in data:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"status": "Missing"}})

    new_status = data["status"]
    if new_status not in [s.value for s in BookingStatus]:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"status": "Invalid status"}})

    new_status = BookingStatus(new_status)
    old_status = booking.status

    # Valid transitions
    valid_transitions = {
        BookingStatus.CONFIRMED: [BookingStatus.COMPLETED, BookingStatus.NO_SHOW, BookingStatus.CANCELLED],
        BookingStatus.PENDING: [BookingStatus.COMPLETED, BookingStatus.NO_SHOW, BookingStatus.CANCELLED],
        BookingStatus.CANCELLED: [],
        BookingStatus.COMPLETED: [],
        BookingStatus.NO_SHOW: [],
    }

    if new_status not in valid_transitions.get(old_status, []):
        raise APIError(422, "INVALID_STATUS_TRANSITION", "Không thể chuyển trạng thái này.", {"from": old_status.value, "to": new_status.value})

    try:
        booking.status = new_status
        booking.updated_at = datetime.now()

        # BR-19: completed -> +1 visit_count
        if new_status == BookingStatus.COMPLETED and old_status in (BookingStatus.CONFIRMED, BookingStatus.PENDING):
            booking.customer.visit_count += 1

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        raise e

    return jsonify({
        "id": booking.id,
        "booking_code": booking.booking_code,
        "status": booking.status.value,
    }), 200


# ============================================================
# 3.7 PATCH /admin/bookings/<id>/reservations/<rid>/therapist
# ============================================================
@api_bp.route(
    "/admin/bookings/<int:booking_id>/reservations/<int:reservation_id>/therapist",
    methods=["PATCH"],
)
@admin_required
def admin_assign_reservation_therapist(booking_id, reservation_id):
    """Admin đổi người phụ trách cho MỘT khách trong booking.

    Nhóm từ 2 người không được chỉ định nhân viên (BR-04) nên BE tự phân công lúc
    tạo booking — đây là chỗ duy nhất sửa lại phân công đó, cũng là cách duy nhất
    để admin xếp người cho nhóm đông.

    KHÔNG đụng `requested_therapist_id`: đó là "khách đã yêu cầu ai", phải giữ
    nguyên để còn đối chiếu với "thực tế ai làm" (BR-21).
    """
    from app.models.shop import Reservation

    booking = Booking.query.get(booking_id)
    if not booking:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    reservation = Reservation.query.filter_by(
        id=reservation_id, booking_id=booking_id
    ).first()
    if not reservation:
        raise APIError(404, "RESOURCE_NOT_FOUND", "Không tìm thấy dữ liệu yêu cầu.")

    # Booking đã huỷ/đã xong thì phân công chỉ còn là dữ liệu lịch sử.
    if booking.status not in (BookingStatus.CONFIRMED, BookingStatus.PENDING):
        raise APIError(
            422,
            "INVALID_STATUS_TRANSITION",
            "Chỉ đổi được nhân viên cho đơn đang chờ hoặc đã xác nhận.",
            {"status": booking.status.value},
        )

    data = request.get_json() or {}
    if "therapist_id" not in data:
        raise APIError(400, "VALIDATION_ERROR", "Dữ liệu không hợp lệ, vui lòng kiểm tra lại.", {"fields": {"therapist_id": "Missing"}})

    therapist = Therapist.query.get(data["therapist_id"])
    if not therapist or therapist.shop_id != booking.shop_id:
        raise APIError(
            422,
            "VALIDATION_ERROR",
            "Nhân viên không thuộc cửa hàng của đơn này.",
            {"fields": {"therapist_id": "Not in this shop"}},
        )

    # Lượt này dài bao lâu — add-on của từng khách khác nhau nên phải cộng riêng.
    dur = reservation.main_course.duration_min + sum(
        a.duration_min for a in reservation.addons
    )
    start_min = time_to_minutes(booking.start_time)
    end_min = start_min + dur

    covered = any(
        time_to_minutes(s.start_time) <= start_min
        and time_to_minutes(s.end_time) >= end_min
        for s in Shift.query.filter_by(
            therapist_id=therapist.id, work_date=booking.booking_date
        ).all()
    )
    if not covered:
        raise APIError(
            422,
            "THERAPIST_OFF_SHIFT",
            f"{therapist.name} không có ca làm phủ hết lượt này. Hãy xếp ca trước.",
            {"required": {
                "date": booking.booking_date.strftime("%Y-%m-%d"),
                "from": f"{start_min // 60:02d}:{start_min % 60:02d}",
                "to": f"{end_min // 60:02d}:{end_min % 60:02d}",
            }},
        )

    # Trùng giờ với lượt khác của chính nhân viên đó — kể cả lượt của khách khác
    # trong CÙNG booking (một người không phục vụ hai khách cùng lúc).
    others = (
        db.session.query(Reservation)
        .join(Booking)
        .filter(
            Reservation.therapist_id == therapist.id,
            Reservation.id != reservation.id,
            Booking.booking_date == booking.booking_date,
            Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.PENDING]),
        )
        .all()
    )
    for r in others:
        o_start = time_to_minutes(r.booking.start_time)
        o_end = o_start + r.main_course.duration_min + sum(
            a.duration_min for a in r.addons
        )
        if max(start_min, o_start) < min(end_min, o_end):
            raise APIError(
                409,
                "SLOT_CONFLICT",
                f"{therapist.name} đã có lượt khác trùng giờ ({r.booking.booking_code}).",
                {"conflict_booking_code": r.booking.booking_code},
            )

    try:
        reservation.therapist_id = therapist.id
        booking.updated_at = datetime.now()
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return jsonify({
        "reservation_id": reservation.id,
        "guest_no": reservation.guest_no,
        "therapist_id": therapist.id,
        "therapist_name": therapist.name,
    }), 200