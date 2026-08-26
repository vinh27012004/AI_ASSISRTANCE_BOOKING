"""Seed dữ liệu test đa dạng cho hệ thống booking.

Chạy từ thư mục shop_api (nơi có .flaskenv), với venv ở gốc repo đã activate:

    python seed_test_data.py

An toàn khi chạy lại (idempotent): mọi bản ghi đều "get-or-create" theo khoá duy
nhất (shop_code, username, phone, (shop,name), booking_code...), nên lần chạy thứ
hai không sinh trùng và không nổ unique constraint. Chỉ THÊM, không xoá dữ liệu cũ.

Mục tiêu: phủ nhiều "loại" data để test — nhiều cửa hàng, customer member có rank
(BR-20), therapist cả hai giới, catalog phong phú, NG list, combo restriction (BR-09),
và booking đủ dạng (đơn/nhóm 2-3, chỉ định đích danh/theo giới, confirmed/completed/
cancelled).
"""

from datetime import date, time, timedelta

from werkzeug.security import generate_password_hash

from app import create_app
from app.extensions import db
from app.models.shop import (
    Account,
    Addon,
    Booking,
    BookingStatus,
    Course,
    Customer,
    Gender,
    MemberType,
    NgList,
    Reservation,
    Role,
    Shift,
    Shop,
    SlotStatus,
    Therapist,
    TimeSlot,
    combo_restriction,
)

# Hôm nay theo bối cảnh dự án; shift/booking trải quanh mốc này.
TODAY = date(2026, 7, 27)
SHIFT_UNTIL = date(2026, 8, 31)
TEST_PASSWORD = "test1234"  # mật khẩu chung cho mọi account test


# --------------------------------------------------------------------------
# Helpers get-or-create
# --------------------------------------------------------------------------
def get_or_create_shop(shop_code, name, address, phone):
    shop = Shop.query.filter_by(shop_code=shop_code).first()
    if shop:
        return shop, False
    shop = Shop(shop_code=shop_code, name=name, address=address, phone=phone)
    db.session.add(shop)
    db.session.flush()
    return shop, True


def get_or_create_course(shop, name, duration_min, price):
    c = Course.query.filter_by(shop_id=shop.id, name=name).first()
    if c:
        return c, False
    c = Course(shop_id=shop.id, name=name, duration_min=duration_min, price=price)
    db.session.add(c)
    db.session.flush()
    return c, True


def get_or_create_addon(shop, name, duration_min, price):
    a = Addon.query.filter_by(shop_id=shop.id, name=name).first()
    if a:
        return a, False
    a = Addon(shop_id=shop.id, name=name, duration_min=duration_min, price=price)
    db.session.add(a)
    db.session.flush()
    return a, True


def get_or_create_therapist(shop, name, gender, username=None):
    t = Therapist.query.filter_by(shop_id=shop.id, name=name).first()
    created = False
    if not t:
        t = Therapist(shop_id=shop.id, name=name, gender=gender)
        db.session.add(t)
        db.session.flush()
        created = True
    if username and not t.account:
        if not Account.query.filter_by(username=username).first():
            db.session.add(Account(
                username=username,
                password_hash=generate_password_hash(TEST_PASSWORD),
                role=Role.THERAPIST,
                therapist_id=t.id,
            ))
            db.session.flush()
    return t, created


def get_or_create_shift(therapist, work_date, start_time, end_time):
    s = Shift.query.filter_by(
        therapist_id=therapist.id, work_date=work_date, start_time=start_time
    ).first()
    if s:
        return s, False
    s = Shift(
        therapist_id=therapist.id,
        work_date=work_date,
        start_time=start_time,
        end_time=end_time,
    )
    db.session.add(s)
    return s, True


def get_or_create_customer(phone, email, member_type, rank, visit_count):
    c = Customer.query.filter_by(phone=phone).first()
    if c:
        return c, False
    c = Customer(
        phone=phone,
        email=email,
        member_type=member_type,
        rank=rank,
        visit_count=visit_count,
    )
    db.session.add(c)
    db.session.flush()
    return c, True


def get_or_create_ng(phone, reason):
    n = NgList.query.filter_by(phone=phone).first()
    if n:
        return n, False
    n = NgList(phone=phone, reason=reason)
    db.session.add(n)
    return n, True


def get_or_create_combo_restriction(course, addon):
    exists = db.session.query(combo_restriction).filter_by(
        course_id=course.id, addon_id=addon.id
    ).first()
    if exists:
        return False
    db.session.execute(
        combo_restriction.insert().values(course_id=course.id, addon_id=addon.id)
    )
    return True


def get_or_create_slot(shop, slot_date, start_time):
    s = TimeSlot.query.filter_by(
        shop_id=shop.id, slot_date=slot_date, start_time=start_time
    ).first()
    if s:
        return s
    s = TimeSlot(
        shop_id=shop.id, slot_date=slot_date, start_time=start_time,
        status=SlotStatus.AVAILABLE,
    )
    db.session.add(s)
    db.session.flush()
    return s


def make_booking(shop, code_suffix, d, t, course, party, therapists, status,
                 customer, addons_per_guest=None, requested_tid=None,
                 requested_gender=None):
    """Tạo booking + reservation nhất quán. therapists: list đúng party phần tử
    (therapist ĐƯỢC PHÂN CÔNG cho từng khách). requested_* chỉ áp cho party==1."""
    code = f"{d:%Y%m%d}-{shop.shop_code}-{code_suffix}"
    existing = Booking.query.filter_by(booking_code=code).first()
    if existing:
        return existing, False

    booking = Booking(
        booking_code=code,
        shop_id=shop.id,
        customer_id=customer.id,
        booking_date=d,
        start_time=t,
        party_size=party,
        status=status,
    )
    db.session.add(booking)
    db.session.flush()

    slot = get_or_create_slot(shop, d, t)
    for j in range(party):
        r = Reservation(
            booking_id=booking.id,
            guest_no=j + 1,
            main_course_id=course.id,
            therapist_id=therapists[j].id,
            slot_id=slot.id,
            requested_therapist_id=(requested_tid if party == 1 and j == 0 else None),
            therapist_gender=(requested_gender if party == 1 and j == 0 else None),
        )
        db.session.add(r)
        db.session.flush()
        if addons_per_guest and addons_per_guest[j]:
            r.addons.extend(addons_per_guest[j])
            db.session.flush()

    # BR-19: completed cộng lượt ghé. Chỉ cộng KHI THỰC SỰ tạo mới -> chạy lại
    # script không cộng dồn oan.
    if status == BookingStatus.COMPLETED:
        customer.visit_count += 1

    return booking, True


# --------------------------------------------------------------------------
# Định nghĩa dữ liệu
# --------------------------------------------------------------------------
# Catalog dùng chung cho các shop mới (2,3,4). (name, duration_min, price VND)
# Tên gói KHÔNG kèm chữ "phút": chỗ hiển thị (chip FE, danh sách bot đọc) đã ghép sẵn
# "· {duration_min} phút", để chữ đó trong tên nữa là lặp hai lần.
COURSE_TEMPLATE = [
    ("Massage body 30", 30, 190000),
    ("Massage body 60", 60, 350000),
    ("Massage body 90", 90, 490000),
    ("Massage body 120", 120, 650000),
    ("Gội đầu dưỡng sinh", 45, 280000),
    ("Massage tinh dầu 90", 90, 590000),
]
ADDON_TEMPLATE = [
    ("Bấm huyệt bàn chân", 15, 80000),
    ("Nệm cao cấp", 15, 60000),
    ("Đá nóng", 15, 90000),
    ("Tinh dầu thơm", 30, 120000),
]

# Therapist mỗi shop: (name, gender, username|None, work_weekdays(set Mon=0..Sun=6),
#                       shift_start, shift_end)
FULL_WEEK = {0, 1, 2, 3, 4, 5, 6}
WEEKDAYS = {0, 1, 2, 3, 4}


def T(hh, mm=0):
    return time(hh, mm)


def main():
    app = create_app()
    with app.app_context():
        stats = {k: 0 for k in [
            "shop", "course", "addon", "therapist", "account", "shift",
            "customer", "ng", "combo", "booking",
        ]}

        # ---- Shop 1 đã tồn tại: bổ sung thêm vài course/addon cho phong phú ----
        shop1 = Shop.query.filter_by(shop_code="1301").first()
        if shop1:
            for name, dur, price in [
                ("Massage body 120", 120, 650000),
                ("Massage tinh dầu 90", 90, 590000),
            ]:
                _, c = get_or_create_course(shop1, name, dur, price)
                stats["course"] += c
            for name, dur, price in [
                ("Đá nóng", 15, 90000),
                ("Tinh dầu thơm", 30, 120000),
            ]:
                _, c = get_or_create_addon(shop1, name, dur, price)
                stats["addon"] += c
            # Thêm 1 combo cấm nữa cho shop 1 (BR-09): Gội đầu dưỡng sinh + Đá nóng
            dhs = Course.query.filter_by(shop_id=shop1.id, name="Gội đầu dưỡng sinh").first()
            hot = Addon.query.filter_by(shop_id=shop1.id, name="Đá nóng").first()
            if dhs and hot:
                stats["combo"] += get_or_create_combo_restriction(dhs, hot)

        # ---- Các shop mới ----
        shops_def = [
            ("1302", "Cửa hàng Hải Châu", "88 Bạch Đằng, Hải Châu, Đà Nẵng", "0236 3812 1302"),
            ("1303", "Cửa hàng Sài Gòn", "45 Lê Lợi, Quận 1, TP. Hồ Chí Minh", "028 3822 1303"),
            ("1304", "Cửa hàng Huế", "20 Hùng Vương, Phường Phú Nhuận, Thành phố Huế", "0234 3845 1304"),
        ]
        shops = {}
        for code, name, addr, phone in shops_def:
            shop, created = get_or_create_shop(code, name, addr, phone)
            shops[code] = shop
            stats["shop"] += created

        # Catalog cho từng shop mới
        catalog = {}  # code -> {"courses": {name: Course}, "addons": {name: Addon}}
        for code, shop in shops.items():
            courses, addons = {}, {}
            for name, dur, price in COURSE_TEMPLATE:
                c, cr = get_or_create_course(shop, name, dur, price)
                courses[name] = c
                stats["course"] += cr
            for name, dur, price in ADDON_TEMPLATE:
                a, cr = get_or_create_addon(shop, name, dur, price)
                addons[name] = a
                stats["addon"] += cr
            catalog[code] = {"courses": courses, "addons": addons}
            # Combo cấm (BR-09): Gội đầu dưỡng sinh + Bấm huyệt bàn chân;
            # Massage tinh dầu 90 + Tinh dầu thơm (đã có tinh dầu, cộng thêm là thừa)
            stats["combo"] += get_or_create_combo_restriction(
                courses["Gội đầu dưỡng sinh"], addons["Bấm huyệt bàn chân"])
            stats["combo"] += get_or_create_combo_restriction(
                courses["Massage tinh dầu 90"], addons["Tinh dầu thơm"])

        # ---- Therapist + account + shift ----
        therapist_defs = {
            "1302": [
                ("Nguyễn Thu Trang", Gender.FEMALE, "trang02", WEEKDAYS, T(10), T(19)),
                ("Trần Quốc Bảo", Gender.MALE, "bao02", {1, 2, 3, 4, 5}, T(11), T(20)),
                ("Lê Ngọc Ánh", Gender.FEMALE, "anh02", {2, 3, 4, 5, 6}, T(10), T(18)),
                ("Phạm Hoàng Long", Gender.MALE, None, {0, 4, 5, 6}, T(12), T(21)),  # chưa có account
            ],
            "1303": [
                ("Vũ Thanh Thảo", Gender.FEMALE, "thao03", FULL_WEEK - {2}, T(10), T(20)),
                ("Đặng Minh Quân", Gender.MALE, "quan03", {0, 1, 2, 3, 4, 5}, T(9), T(17)),
                ("Bùi Khánh Linh", Gender.FEMALE, "linh03", {3, 4, 5, 6, 0}, T(13), T(22)),
                ("Hoàng Đại Nghĩa", Gender.MALE, "nghia03", {5, 6}, T(10), T(22)),  # cuối tuần
                ("Ngô Phương Nhi", Gender.FEMALE, None, WEEKDAYS, T(10), T(16)),  # chưa có account
            ],
            # Shop 4 Huế: CÓ therapist nhưng KHÔNG có ca -> test SHOP_CLOSED
            "1304": [
                ("Lý Gia Huy", Gender.MALE, "huy04", set(), None, None),
                ("Trịnh Mỹ Duyên", Gender.FEMALE, None, set(), None, None),
            ],
        }
        therapists = {}  # (code, name) -> Therapist
        for code, defs in therapist_defs.items():
            shop = shops[code]
            for name, gender, username, weekdays, s_start, s_end in defs:
                had_account_before = False
                existing_t = Therapist.query.filter_by(shop_id=shop.id, name=name).first()
                if existing_t and existing_t.account:
                    had_account_before = True
                t, created = get_or_create_therapist(shop, name, gender, username)
                therapists[(code, name)] = t
                stats["therapist"] += created
                if username and not had_account_before:
                    stats["account"] += 1
                # Sinh ca theo weekday pattern
                if weekdays and s_start and s_end:
                    d = TODAY
                    while d <= SHIFT_UNTIL:
                        if d.weekday() in weekdays:
                            _, sc = get_or_create_shift(t, d, s_start, s_end)
                            stats["shift"] += sc
                        d += timedelta(days=1)

        # ---- Customers: member có rank (BR-20) + vài guest ----
        customers_def = [
            # phone, email, type, rank, visits
            ("0901110001", "gold.member@vidu.com", MemberType.MEMBER, "Gold", 12),
            ("0901110002", "silver.member@vidu.com", MemberType.MEMBER, "Silver", 6),
            ("0901110003", "platinum.member@vidu.com", MemberType.MEMBER, "Platinum", 30),
            ("0901110004", "bronze.member@vidu.com", MemberType.MEMBER, "Bronze", 2),
            ("0901110005", "gold2.member@vidu.com", MemberType.MEMBER, "Gold", 18),
            ("0901110006", "diamond.member@vidu.com", MemberType.MEMBER, "Diamond", 55),
            ("0902220001", "guest.a@vidu.com", MemberType.GUEST, None, 0),
            ("0902220002", "guest.b@vidu.com", MemberType.GUEST, None, 1),
            ("0703330001", "regular.c@vidu.com", MemberType.MEMBER, "Regular", 3),
            ("0703330002", "guest.d@vidu.com", MemberType.GUEST, None, 0),
        ]
        cust = {}
        for phone, email, mtype, rank, visits in customers_def:
            c, created = get_or_create_customer(phone, email, mtype, rank, visits)
            cust[phone] = c
            stats["customer"] += created

        # ---- NG list (BR-06) ----
        for phone, reason in [
            ("0909990001", "Nhiều lần no-show liên tiếp"),
            ("0909990002", "Hành vi không phù hợp với nhân viên"),
            ("0709990003", "Khách tự yêu cầu chặn đặt online"),
        ]:
            _, cr = get_or_create_ng(phone, reason)
            stats["ng"] += cr

        db.session.flush()

        # ---- Bookings đa dạng cho shop 2 & 3 ----
        c2 = catalog["1302"]["courses"]
        a2 = catalog["1302"]["addons"]
        c3 = catalog["1303"]["courses"]
        a3 = catalog["1303"]["addons"]
        s2, s3 = shops["1302"], shops["1303"]

        def th(code, name):
            return therapists[(code, name)]

        bookings_plan = [
            # shop, suffix, date, time, course, party, [therapists], status, customer, addons, req_tid, req_gender
            (s2, "SEED1", date(2026, 7, 28), T(14), c2["Massage body 60"], 1,
             [th("1302", "Nguyễn Thu Trang")], BookingStatus.CONFIRMED, cust["0901110001"],
             None, None, None),
            (s2, "SEED2", date(2026, 7, 29), T(11), c2["Massage body 60"], 2,
             [th("1302", "Nguyễn Thu Trang"), th("1302", "Trần Quốc Bảo")], BookingStatus.CONFIRMED,
             cust["0901110002"], None, None, None),
            (s2, "SEED3", date(2026, 7, 30), T(16), c2["Gội đầu dưỡng sinh"], 1,
             [th("1302", "Lê Ngọc Ánh")], BookingStatus.CONFIRMED, cust["0902220001"],
             [[a2["Nệm cao cấp"]]], None, Gender.FEMALE),  # chỉ định theo giới nữ
            (s2, "SEED4", date(2026, 7, 27), T(10), c2["Massage body 90"], 1,
             [th("1302", "Nguyễn Thu Trang")], BookingStatus.COMPLETED, cust["0901110003"],
             None, None, None),
            (s2, "SEED5", date(2026, 8, 3), T(13), c2["Massage body 60"], 1,
             [th("1302", "Nguyễn Thu Trang")], BookingStatus.CANCELLED, cust["0703330002"],
             None, None, None),

            (s3, "SEED1", date(2026, 7, 28), T(15), c3["Massage tinh dầu 90"], 1,
             [th("1303", "Vũ Thanh Thảo")], BookingStatus.CONFIRMED, cust["0901110005"],
             None, None, None),
            (s3, "SEED2", date(2026, 8, 1), T(12), c3["Massage body 60"], 3,
             [th("1303", "Vũ Thanh Thảo"), th("1303", "Đặng Minh Quân"), th("1303", "Hoàng Đại Nghĩa")],
             BookingStatus.CONFIRMED, cust["0901110002"], None, None, None),
            (s3, "SEED3", date(2026, 7, 31), T(18), c3["Massage body 90"], 1,
             [th("1303", "Bùi Khánh Linh")], BookingStatus.CONFIRMED, cust["0902220002"],
             None, th("1303", "Bùi Khánh Linh").id, None),  # chỉ định ĐÍCH DANH Khánh Linh
            (s3, "SEED4", date(2026, 7, 27), T(11), c3["Massage body 60"], 1,
             [th("1303", "Đặng Minh Quân")], BookingStatus.COMPLETED, cust["0703330001"],
             None, None, None),
        ]
        for (shop, suffix, d, t, course, party, ths, status, customer,
             addons, req_tid, req_gender) in bookings_plan:
            _, created = make_booking(
                shop, suffix, d, t, course, party, ths, status, customer,
                addons_per_guest=addons, requested_tid=req_tid,
                requested_gender=req_gender,
            )
            stats["booking"] += created

        db.session.commit()

        # ---- Tổng kết ----
        print("=== ĐÃ THÊM (bản ghi mới lần chạy này) ===")
        for k, v in stats.items():
            print(f"  {k:12s}: +{v}")
        print("\n=== TỔNG SỐ HIỆN TẠI ===")
        for m in [Shop, Course, Addon, Therapist, Shift, Account,
                  Customer, NgList, Booking, Reservation]:
            print(f"  {m.__name__:12s}: {m.query.count()}")
        print(f"  {'combo_restr':12s}: {db.session.query(combo_restriction).count()}")
        print(f"\nMật khẩu mọi account test: {TEST_PASSWORD!r}")


if __name__ == "__main__":
    main()
