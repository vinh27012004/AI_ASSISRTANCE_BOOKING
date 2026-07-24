"""Models cho hệ thống booking massage — map 1:1 với erd-schema.sql."""

from __future__ import annotations

import enum
from datetime import date, datetime, time

import sqlalchemy as sa
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


def _enum(enum_cls: type[enum.Enum], name: str) -> sa.Enum:
    """ENUM lưu theo *value* ('male') thay vì tên member ('MALE') — mặc định của
    SQLAlchemy là lưu tên, sẽ lệch với schema."""
    return sa.Enum(
        enum_cls,
        name=name,
        values_callable=lambda cls: [member.value for member in cls],
    )


class Gender(str, enum.Enum):
    MALE = "male"
    FEMALE = "female"


class Role(str, enum.Enum):
    ADMIN = "admin"
    THERAPIST = "therapist"


class MemberType(str, enum.Enum):
    MEMBER = "member"
    GUEST = "guest"


class SlotStatus(str, enum.Enum):
    AVAILABLE = "available"
    BOOKED = "booked"


class BookingStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


# --- Bảng nối thuần: composite PK, không có cột phụ ---

combo_restriction = sa.Table(
    "combo_restriction",
    db.metadata,
    sa.Column("course_id", sa.Integer, ForeignKey("course.id"), primary_key=True),
    sa.Column("addon_id", sa.Integer, ForeignKey("addon.id"), primary_key=True),
    comment="BR-09: tổ hợp course + add-on KHÔNG được phép; bảng rỗng = mọi combo hợp lệ",
)

reservation_addon = sa.Table(
    "reservation_addon",
    db.metadata,
    sa.Column(
        "reservation_id",
        sa.Integer,
        ForeignKey("reservation.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    sa.Column("addon_id", sa.Integer, ForeignKey("addon.id"), primary_key=True),
)


class Shop(db.Model):
    __tablename__ = "shop"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_code: Mapped[str] = mapped_column(sa.String(20), unique=True, comment="Mã cửa hàng")
    name: Mapped[str] = mapped_column(sa.String(100))
    address: Mapped[str] = mapped_column(sa.String(255))
    phone: Mapped[str] = mapped_column(sa.String(20))

    courses: Mapped[list[Course]] = relationship(back_populates="shop")
    addons: Mapped[list[Addon]] = relationship(back_populates="shop")
    therapists: Mapped[list[Therapist]] = relationship(back_populates="shop")
    time_slots: Mapped[list[TimeSlot]] = relationship(back_populates="shop")
    bookings: Mapped[list[Booking]] = relationship(back_populates="shop")


class Course(db.Model):
    """Course chính (もみほぐし, ドライヘッドスパ...)."""

    __tablename__ = "course"
    __table_args__ = (
        # BR-02. Dùng MOD() thay cho `%`: Alembic autogenerate render `%` thành `%%`
        # vào source migration -> escape hai lần -> DDL hỏng.
        sa.CheckConstraint("MOD(duration_min, 15) = 0", name="chk_course_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shop.id"))
    name: Mapped[str] = mapped_column(sa.String(100))
    duration_min: Mapped[int] = mapped_column(comment="Bội số 15 phút")
    price: Mapped[int] = mapped_column(comment="JPY")
    is_active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    shop: Mapped[Shop] = relationship(back_populates="courses")
    forbidden_addons: Mapped[list[Addon]] = relationship(
        secondary=combo_restriction, back_populates="forbidden_courses"
    )
    reservations: Mapped[list[Reservation]] = relationship(back_populates="main_course")


class Addon(db.Model):
    """Add-on (足つぼ, プレミアムマットレス...) — chỉ đi kèm course chính (BR-01)."""

    __tablename__ = "addon"
    __table_args__ = (
        # BR-02 — xem ghi chú MOD() ở Course.
        sa.CheckConstraint("MOD(duration_min, 15) = 0", name="chk_addon_duration"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shop.id"))
    name: Mapped[str] = mapped_column(sa.String(100))
    duration_min: Mapped[int] = mapped_column(comment="Bội số 15 phút")
    price: Mapped[int] = mapped_column(comment="JPY")
    is_active: Mapped[bool] = mapped_column(default=True, server_default=sa.true())

    shop: Mapped[Shop] = relationship(back_populates="addons")
    forbidden_courses: Mapped[list[Course]] = relationship(
        secondary=combo_restriction, back_populates="forbidden_addons"
    )
    reservations: Mapped[list[Reservation]] = relationship(
        secondary=reservation_addon, back_populates="addons"
    )


class Therapist(db.Model):
    __tablename__ = "therapist"

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shop.id"))
    name: Mapped[str] = mapped_column(sa.String(100))
    gender: Mapped[Gender] = mapped_column(_enum(Gender, "therapist_gender_enum"))

    shop: Mapped[Shop] = relationship(back_populates="therapists")
    shifts: Mapped[list[Shift]] = relationship(back_populates="therapist")
    account: Mapped[Account | None] = relationship(back_populates="therapist")
    # reservation có 2 FK trỏ về therapist (phân công + chỉ định) nên phải nói rõ
    # đây là phía PHÂN CÔNG — nguồn dữ liệu của trang therapist (US-08).
    reservations: Mapped[list[Reservation]] = relationship(
        back_populates="therapist", foreign_keys="Reservation.therapist_id"
    )


class Shift(db.Model):
    """Ca làm việc của therapist."""

    __tablename__ = "shift"
    __table_args__ = (
        sa.UniqueConstraint("therapist_id", "work_date", "start_time", name="uq_shift"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    therapist_id: Mapped[int] = mapped_column(ForeignKey("therapist.id"))
    work_date: Mapped[date]
    start_time: Mapped[time]
    end_time: Mapped[time]

    therapist: Mapped[Therapist] = relationship(back_populates="shifts")


class Account(db.Model):
    """Tài khoản đăng nhập nội bộ: admin quản lý cửa hàng, therapist dùng tài khoản
    do admin cấp."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(sa.String(50), unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "account_role_enum"))
    therapist_id: Mapped[int | None] = mapped_column(
        ForeignKey("therapist.id"), comment="Chỉ với role therapist"
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    therapist: Mapped[Therapist | None] = relationship(back_populates="account")


class Customer(db.Model):
    """Khách hàng — nhận dạng qua SĐT."""

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(20), unique=True)
    email: Mapped[str] = mapped_column(
        sa.String(255), comment="Xác thực + nhận mã đặt chỗ (BR-15)"
    )
    member_type: Mapped[MemberType] = mapped_column(
        _enum(MemberType, "member_type_enum"),
        default=MemberType.GUEST,
        server_default=MemberType.GUEST.value,
    )
    rank: Mapped[str | None] = mapped_column(
        sa.String(20), comment="Chỉ để hiển thị (BR-20); giả định chỉ member có"
    )
    visit_count: Mapped[int] = mapped_column(default=0, server_default=sa.text("0"))

    bookings: Mapped[list[Booking]] = relationship(back_populates="customer")


class NgList(db.Model):
    """Danh sách SĐT bị cấm (BR-06)."""

    __tablename__ = "ng_list"

    id: Mapped[int] = mapped_column(primary_key=True)
    phone: Mapped[str] = mapped_column(sa.String(20), unique=True)
    reason: Mapped[str | None] = mapped_column(
        sa.String(255), comment="Lý do cấm - có hiển thị (BR-20)"
    )
    added_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class TimeSlot(db.Model):
    """Khung giờ khả dụng — hệ thống tự quản lý, cập nhật thời gian thực (BR-07, BR-08)."""

    __tablename__ = "time_slot"
    __table_args__ = (
        sa.UniqueConstraint("shop_id", "slot_date", "start_time", name="uq_slot"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    shop_id: Mapped[int] = mapped_column(ForeignKey("shop.id"))
    slot_date: Mapped[date]
    start_time: Mapped[time]
    status: Mapped[SlotStatus] = mapped_column(
        _enum(SlotStatus, "slot_status_enum"),
        default=SlotStatus.AVAILABLE,
        server_default=SlotStatus.AVAILABLE.value,
    )

    shop: Mapped[Shop] = relationship(back_populates="time_slots")
    reservations: Mapped[list[Reservation]] = relationship(back_populates="slot")


class Booking(db.Model):
    """Một lần đặt (1 cuộc gọi, 1 mã POS)."""

    __tablename__ = "booking"
    __table_args__ = (
        sa.CheckConstraint("party_size BETWEEN 1 AND 3", name="chk_party_size"),  # BR-14
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_code: Mapped[str | None] = mapped_column(
        sa.String(30),
        unique=True,
        comment="BE sinh: {yyyyMMdd}-{shop_code}-{random} (BR-12)",
    )
    shop_id: Mapped[int] = mapped_column(ForeignKey("shop.id"))
    customer_id: Mapped[int] = mapped_column(ForeignKey("customer.id"))
    booking_date: Mapped[date]
    start_time: Mapped[time]
    party_size: Mapped[int] = mapped_column(
        default=1,
        server_default=sa.text("1"),
        comment="1-3; >=2 là booking nhóm (BR-14)",
    )
    status: Mapped[BookingStatus] = mapped_column(
        _enum(BookingStatus, "booking_status_enum"),
        default=BookingStatus.PENDING,
        server_default=BookingStatus.PENDING.value,
        comment="COMPLETED tự động +1 visit_count (BR-19); no_show: giả định",
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    updated_at: Mapped[datetime | None] = mapped_column(onupdate=sa.func.now())

    shop: Mapped[Shop] = relationship(back_populates="bookings")
    customer: Mapped[Customer] = relationship(back_populates="bookings")
    reservations: Mapped[list[Reservation]] = relationship(
        back_populates="booking",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Reservation.guest_no",
    )


class Reservation(db.Model):
    """Suất phục vụ của TỪNG NGƯỜI trong booking (BR-10): booking 1 người = 1
    reservation; nhóm N người = N reservation cùng giờ.

    CHỈ ĐỊNH (khách yêu cầu) và PHÂN CÔNG (BE tính) là hai khái niệm khác nhau — BR-21:

    - Chỉ định  (`requested_therapist_id` / `therapist_gender`): ý muốn của khách.
      Tùy chọn, thường NULL, chỉ booking 1 người (BR-04). Là ràng buộc ĐẦU VÀO khi tìm slot.
    - Phân công (`therapist_id`): kết quả BE tính lúc tạo. Luôn có, cho mọi người trong nhóm.
      Là nguồn dữ liệu của BR-03 (đếm therapist bận) và trang therapist (US-08).

    Gộp chung một cột thì không phân biệt được "khách đòi Hana" với "BE tự phân Hana" →
    sửa booking không biết có được đổi người hay không (US-02 AC2).
    """

    __tablename__ = "reservation"
    __table_args__ = (
        sa.UniqueConstraint("booking_id", "guest_no", name="uq_reservation"),
        # Hai KIỂU CHỈ ĐỊNH loại trừ nhau: hoặc đích danh, hoặc giới tính.
        # KHÔNG áp cho therapist_id — "khách yêu cầu nữ" + "BE phân Hana" là hợp lệ.
        sa.CheckConstraint(
            "requested_therapist_id IS NULL OR therapist_gender IS NULL",
            name="chk_therapist_exclusive",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("booking.id", ondelete="CASCADE"))
    guest_no: Mapped[int] = mapped_column(comment="Người thứ mấy trong nhóm (1..party_size)")
    main_course_id: Mapped[int] = mapped_column(ForeignKey("course.id"))

    # --- Khách yêu cầu gì (input) ---
    requested_therapist_id: Mapped[int | None] = mapped_column(
        ForeignKey("therapist.id"),
        comment="Khách chỉ định ĐÍCH DANH — chỉ booking 1 người (BR-04, BR-05)",
    )
    therapist_gender: Mapped[Gender | None] = mapped_column(
        _enum(Gender, "reservation_therapist_gender_enum"),
        comment="Khách chỉ định theo GIỚI TÍNH — loại trừ với requested_therapist_id",
    )

    # --- Hệ thống phân công ai (output) ---
    therapist_id: Mapped[int | None] = mapped_column(
        ForeignKey("therapist.id"),
        comment="Therapist THỰC SỰ phục vụ suất này, BE tính lúc tạo (BR-21). "
        "NULL chỉ còn ở data cũ trước BR-21",
    )

    slot_id: Mapped[int | None] = mapped_column(ForeignKey("time_slot.id"))

    booking: Mapped[Booking] = relationship(back_populates="reservations")
    main_course: Mapped[Course] = relationship(back_populates="reservations")
    therapist: Mapped[Therapist | None] = relationship(
        back_populates="reservations", foreign_keys=[therapist_id]
    )
    requested_therapist: Mapped[Therapist | None] = relationship(
        foreign_keys=[requested_therapist_id]
    )
    slot: Mapped[TimeSlot | None] = relationship(back_populates="reservations")
    addons: Mapped[list[Addon]] = relationship(
        secondary=reservation_addon, back_populates="reservations"
    )
