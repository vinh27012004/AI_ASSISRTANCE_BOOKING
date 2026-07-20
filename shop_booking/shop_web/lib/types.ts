export type Gender = "male" | "female";

export type BookingStatus =
  | "pending"
  | "confirmed"
  | "cancelled"
  | "completed"
  | "no_show";

export type Shop = {
  id: number;
  shop_code: string;
  name: string;
  address: string;
  phone: string;
};

export type Course = {
  id: number;
  name: string;
  duration_min: number;
  price: number;
};

export type Addon = Course & {
  /** Course không được ghép cùng add-on này (BR-09) — FE disable sớm. */
  restricted_course_ids: number[];
};

export type ServicesResponse = {
  courses: Course[];
  addons: Addon[];
  /** "SHOP_CLOSED" = ngày đó shop không phục vụ (case A1). */
  reason: "SHOP_CLOSED" | null;
};

export type SlotsResponse = {
  slots: string[];
};

export type CustomerInfo = {
  member_type: "member" | "guest";
  rank: string | null;
  visit_count: number;
};

export type ReservationInput = {
  addon_ids: number[];
};

export type BookingCreateRequest = {
  shop_id: number;
  date: string;
  start_time: string;
  party_size: number;
  phone: string;
  email: string;
  course_id: number;
  reservations: ReservationInput[];
  therapist_id?: number | null;
  therapist_gender?: Gender | null;
};

export type BookingReservation = {
  guest_no: number;
  addons: Course[];
};

/**
 * Schema `Booking` dùng chung cho POST /bookings, POST /bookings/retrieve,
 * PATCH /bookings/{code} và POST /bookings/{code}/cancel.
 */
export type Booking = {
  booking_code: string;
  status: BookingStatus;
  shop: Shop;
  date: string;
  start_time: string;
  party_size: number;
  course: Course | null;
  reservations: BookingReservation[];
  /** BR-21 — ai thực sự phục vụ; BE chỉ trả khi đi 1 người. */
  therapist_name: string | null;
  /** BR-04 — khách đã chỉ định gì; trả đúng tên field mà PATCH nhận lại. */
  requested_therapist_id: number | null;
  requested_therapist_gender: Gender | null;
  /** BR-16 — BE tự tính theo giờ máy chủ, FE không tự trừ ngày giờ. */
  can_modify: boolean;
};

/** BR-17 — chỉ POST /bookings cấp edit_token (cửa sổ sửa nhanh 2 phút). */
export type BookingCreated = Booking & {
  edit_token: string;
  edit_token_expires_in: number;
};

export type BookingUpdateRequest = {
  date?: string;
  start_time?: string;
  party_size?: number;
  course_id?: number;
  reservations?: ReservationInput[];
  therapist_id?: number | null;
  therapist_gender?: Gender | null;
  /** Bắt buộc khi xác thực bằng mã + email (không có edit_token). */
  email?: string;
};

export type Therapist = {
  id: number;
  name: string;
  gender: Gender;
};

export type TherapistsResponse = {
  therapists: Therapist[];
};

/** Một khoảng giờ "HH:MM" – "HH:MM" trên timeline. */
export type TimelineRange = {
  start_time: string;
  end_time: string;
};

export type TimelineBooking = TimelineRange & {
  /** BE chỉ trả tên course — không bao giờ có thông tin khách đặt. */
  course_name: string;
};

export type TimelineTherapist = Therapist & {
  shifts: TimelineRange[];
  bookings: TimelineBooking[];
};

/** GET /shops/{id}/timeline — lịch trong ngày theo từng nhân viên (wireframe 02). */
export type TimelineResponse = {
  date: string;
  therapists: TimelineTherapist[];
};

export type LoginResponse = {
  access_token: string;
  role: "admin" | "therapist";
  therapist_id: number | null;
  username: string;
  /** Therapist thì là tên thật, admin thì trùng `username`. */
  display_name: string;
  expires_in: number;
};

export type AdminBookingItem = {
  id: number;
  booking_code: string;
  status: BookingStatus;
  date: string;
  start_time: string;
  party_size: number;
  customer: {
    phone: string;
    email: string;
    member_type: "member" | "guest";
    rank: string | null;
    visit_count: number;
  };
  course: Course | null;
  reservations: {
    id: number;
    guest_no: number;
    /** Course + add-on RIÊNG của khách này, nên mỗi suất một khác. */
    duration_min: number;
    /** BE phân công (BR-21) — admin sửa được. */
    therapist_id: number | null;
    therapist_name: string | null;
    /** Khách chỉ định đích danh ai, chỉ có ở booking 1 người (BR-04). */
    requested_therapist_name: string | null;
    addons: { id: number; name: string }[];
  }[];
};

export type AdminBookingsResponse = {
  items: AdminBookingItem[];
  page: number;
  per_page: number;
  total: number;
};

export type AdminTherapist = {
  id: number;
  name: string;
  gender: Gender;
  /** BE không bao giờ trả `username`/`password_hash` — chỉ cho biết có hay chưa. */
  has_account: boolean;
};

export type AdminAccountInput = {
  username: string;
  password: string;
};

export type AdminTherapistCreateRequest = {
  shop_id: number;
  name: string;
  gender: Gender;
  /** Cấp tài khoản đăng nhập luôn lúc tạo. */
  account?: AdminAccountInput;
};

export type AdminTherapistUpdateRequest = {
  name?: string;
  gender?: Gender;
  /** Cấp tài khoản cho nhân viên CHƯA có; đã có rồi thì BE trả 409 ACCOUNT_EXISTS. */
  account?: AdminAccountInput;
  /** Đặt lại mật khẩu cho nhân viên ĐÃ có; chưa có thì BE trả 409 ACCOUNT_MISSING. */
  reset_password?: string;
};

export type AdminShift = {
  id: number;
  therapist_id: number;
  therapist_name: string;
  work_date: string;
  start_time: string;
  end_time: string;
};

export type AdminNgItem = {
  id: number;
  phone: string;
  reason: string | null;
  added_at: string;
};

export type AdminCourse = {
  id: number;
  name: string;
  duration_min: number;
  price: number;
  is_active: boolean;
  shop_id: number;
};

export type AdminAddon = AdminCourse;

export type AdminComboRestriction = {
  course_id: number;
  course_name: string | null;
  addon_id: number;
  addon_name: string | null;
};

export type TherapistScheduleBooking = {
  start_time: string;
  duration_min: number;
  course_name: string;
  addon_names: string[];
  guest_no: number;
  party_size: number;
  customer_phone_masked: string;
};

export type TherapistScheduleResponse = {
  date: string;
  shifts: { start_time: string; end_time: string }[];
  bookings: TherapistScheduleBooking[];
};
