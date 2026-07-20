import type {
  Booking,
  BookingCreateRequest,
  BookingCreated,
  BookingUpdateRequest,
  CustomerInfo,
  Gender,
  ServicesResponse,
  Shop,
  SlotsResponse,
  TherapistsResponse,
  TimelineResponse,
  LoginResponse,
  TherapistScheduleResponse,
  AdminBookingsResponse,
  AdminTherapist,
  AdminTherapistCreateRequest,
  AdminTherapistUpdateRequest,
  AdminShift,
  AdminNgItem,
  AdminCourse,
  AdminAddon,
  AdminComboRestriction,
} from "./types";

const BASE = "/api/v1";

/**
 * Lỗi đã chuẩn hoá theo envelope của BE: {error: {code, message, details}}.
 * `message` luôn là text BE trả — FE hiển thị thẳng, không tự bịa (quyết định thiết kế #7).
 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown> | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

const FALLBACK_MESSAGE =
  "Hệ thống đang gặp sự cố tạm thời. Vui lòng thử lại sau ít phút.";

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export function toApiError(error: unknown): ApiError {
  if (error instanceof ApiError) return error;
  return new ApiError(0, "INTERNAL_ERROR", FALLBACK_MESSAGE);
}

type ErrorBody = {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
};

/**
 * Những gì lưu ở localStorage sau khi đăng nhập. `display_name` để hiện góc phải
 * — token là JWT chứ không phải nơi tra cứu tên, và gọi API chỉ để lấy tên thì
 * mỗi lần tải trang lại chớp một nhịp "chưa biết bạn là ai".
 *
 * Dữ liệu cũ (trước khi có tên) thiếu hai trường này nên đều phải optional; chỗ
 * đọc tự lùi về `username` rồi mới tới role.
 */
export type StoredUser = {
  role: "admin" | "therapist";
  therapist_id: number | null;
  username?: string;
  display_name?: string;
};

export const authStorage = {
  getToken() {
    if (typeof window === "undefined") return null;
    return localStorage.getItem("access_token");
  },
  setToken(token: string) {
    if (typeof window === "undefined") return;
    localStorage.setItem("access_token", token);
  },
  clearToken() {
    if (typeof window === "undefined") return;
    localStorage.removeItem("access_token");
  },
  getUser() {
    if (typeof window === "undefined") return null;
    const userStr = localStorage.getItem("user_info");
    return userStr ? JSON.parse(userStr) as StoredUser : null;
  },
  setUser(user: StoredUser) {
    if (typeof window === "undefined") return;
    localStorage.setItem("user_info", JSON.stringify(user));
  },
  clearUser() {
    if (typeof window === "undefined") return;
    localStorage.removeItem("user_info");
  }
};

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let res: Response;
  const token = authStorage.getToken();
  const authHeader: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...authHeader,
        ...(init.headers as Record<string, string>),
      },
    });
  } catch (error) {
    // AbortError phải bay lên nguyên vẹn để caller bỏ qua, không hiện lỗi.
    if (isAbort(error)) throw error;
    throw new ApiError(
      0,
      "NETWORK_ERROR",
      "Không kết nối được tới máy chủ. Vui lòng kiểm tra kết nối mạng và thử lại.",
    );
  }

  const body: unknown = await res.json().catch(() => null);

  if (!res.ok) {
    const payload = (body ?? {}) as ErrorBody;
    const code = payload.error?.code ?? "INTERNAL_ERROR";

    // Token của mình bị BE từ chối (hết hạn, đổi SECRET_KEY, tài khoản bị xoá).
    // AuthGuard chỉ kiểm localStorage CÓ token hay không, chứ không biết token còn
    // sống — không dọn ở đây thì khách kẹt lại trang admin trắng trơn kèm dòng
    // "Thông tin đăng nhập không đúng", chẳng hiểu vì sao và cũng không có lối ra.
    //
    // Bắt buộc phải có `token`: khách vãng lai sửa booking bằng edit_token hết hạn
    // cũng ăn 401, nhưng họ có đăng nhập đâu mà đá về /login.
    if (res.status === 401 && token && code === "UNAUTHORIZED") {
      authStorage.clearToken();
      authStorage.clearUser();
      if (
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/login")
      ) {
        window.location.href = "/login";
      }
    }

    throw new ApiError(
      res.status,
      code,
      payload.error?.message ?? FALLBACK_MESSAGE,
      payload.error?.details ?? null,
    );
  }

  return body as T;
}

function jsonPost(payload: unknown, extraHeaders: Record<string, string> = {}) {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(payload),
  } satisfies RequestInit;
}

export type SlotQuery = {
  date: string;
  partySize: number;
  courseId: number;
  addonIds: number[];
  /** BE từ chối nếu gửi kèm therapistId — chỉ được chọn một trong hai. */
  therapistGender: Gender | null;
  therapistId: number | null;
};

export const api = {
  shops(signal?: AbortSignal) {
    return request<Shop[]>("/shops", { signal });
  },

  services(
    shopId: number,
    params: { date: string; partySize: number },
    signal?: AbortSignal,
  ) {
    const qs = new URLSearchParams({
      date: params.date,
      party_size: String(params.partySize),
    });
    return request<ServicesResponse>(`/shops/${shopId}/services?${qs}`, {
      signal,
    });
  },

  slots(shopId: number, params: SlotQuery, signal?: AbortSignal) {
    const qs = new URLSearchParams({
      date: params.date,
      party_size: String(params.partySize),
      course_id: String(params.courseId),
    });
    // BE đọc addon_ids dạng "7,8" (style=form, explode=false).
    if (params.addonIds.length > 0) {
      qs.set("addon_ids", params.addonIds.join(","));
    }
    if (params.therapistGender) {
      qs.set("therapist_gender", params.therapistGender);
    }
    if (params.therapistId) {
      qs.set("therapist_id", String(params.therapistId));
    }
    return request<SlotsResponse>(`/shops/${shopId}/slots?${qs}`, { signal });
  },

  /**
   * Nhân viên của shop cho bước chỉ định đích danh (BR-04).
   * Truyền `date` để BE loại sẵn người không có ca hôm đó (case A4).
   */
  therapists(shopId: number, date: string | null, signal?: AbortSignal) {
    const qs = new URLSearchParams();
    if (date) qs.set("date", date);
    return request<TherapistsResponse>(`/shops/${shopId}/therapists?${qs}`, {
      signal,
    });
  },

  /** Lịch trong ngày theo từng nhân viên — vẽ timeline ở bước Booking. */
  timeline(shopId: number, date: string, signal?: AbortSignal) {
    const qs = new URLSearchParams({ date });
    return request<TimelineResponse>(`/shops/${shopId}/timeline?${qs}`, {
      signal,
    });
  },

  lookupCustomer(phone: string, signal?: AbortSignal) {
    return request<CustomerInfo>("/customers/lookup", {
      ...jsonPost({ phone }),
      signal,
    });
  },

  createBooking(payload: BookingCreateRequest, idempotencyKey: string) {
    return request<BookingCreated>(
      "/bookings",
      jsonPost(payload, { "Idempotency-Key": idempotencyKey }),
    );
  },

  /** Tra cứu đặt chỗ bằng mã + email (trang Quản lý đặt chỗ). */
  retrieveBooking(
    payload: { booking_code: string; email: string },
    signal?: AbortSignal,
  ) {
    return request<Booking>("/bookings/retrieve", {
      ...jsonPost(payload),
      signal,
    });
  },

  /**
   * Sửa đặt chỗ. Hai cách xác thực:
   * - `editToken` (cửa sổ nhanh 2 phút sau khi tạo — BR-17) → gửi header X-Edit-Token.
   * - không có token → `payload.email` phải khớp email của booking.
   */
  updateBooking(
    bookingCode: string,
    payload: BookingUpdateRequest,
    editToken?: string | null,
  ) {
    return request<Booking>(`/bookings/${bookingCode}`, {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        ...(editToken ? { "X-Edit-Token": editToken } : {}),
      },
      body: JSON.stringify(payload),
    });
  },

  /** Huỷ đặt chỗ. BE idempotent: huỷ lần hai vẫn trả 200. */
  cancelBooking(bookingCode: string, email: string) {
    return request<Booking>(
      `/bookings/${bookingCode}/cancel`,
      jsonPost({ email }),
    );
  },

  // --- AUTH ---
  login(payload: Record<string, string>) {
    return request<LoginResponse>("/auth/login", jsonPost(payload));
  },

  // --- THERAPIST SCHEDULE ---
  therapistSchedule(params: { date: string }, signal?: AbortSignal) {
    const qs = new URLSearchParams({ date: params.date });
    return request<TherapistScheduleResponse>(`/therapists/me/schedule?${qs}`, { signal });
  },

  // --- ADMIN BOOKINGS ---
  adminListBookings(
    params: { shop_id: number; date?: string; status?: string; page?: number; per_page?: number },
    signal?: AbortSignal
  ) {
    const qs = new URLSearchParams({ shop_id: String(params.shop_id) });
    if (params.date) qs.set("date", params.date);
    if (params.status) qs.set("status", params.status);
    if (params.page) qs.set("page", String(params.page));
    if (params.per_page) qs.set("per_page", String(params.per_page));
    return request<AdminBookingsResponse>(`/admin/bookings?${qs}`, { signal });
  },

  /** Đổi người phụ trách một suất khách. BE chặn nếu lệch ca hoặc trùng lịch. */
  adminAssignTherapist(bookingId: number, reservationId: number, therapistId: number) {
    return request<{
      reservation_id: number;
      guest_no: number;
      therapist_id: number;
      therapist_name: string;
    }>(`/admin/bookings/${bookingId}/reservations/${reservationId}/therapist`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ therapist_id: therapistId }),
    });
  },

  adminUpdateBookingStatus(bookingId: number, status: string) {
    return request<{ id: number; booking_code: string; status: string }>(
      `/admin/bookings/${bookingId}/status`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      }
    );
  },

  // --- ADMIN THERAPISTS ---
  adminListTherapists(shopId: number, signal?: AbortSignal) {
    return request<AdminTherapist[]>(`/admin/therapists?shop_id=${shopId}`, { signal });
  },

  adminCreateTherapist(payload: AdminTherapistCreateRequest) {
    return request<AdminTherapist>("/admin/therapists", jsonPost(payload));
  },

  adminUpdateTherapist(id: number, payload: AdminTherapistUpdateRequest) {
    return request<AdminTherapist>(`/admin/therapists/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  adminDeleteTherapist(id: number) {
    return request<void>(`/admin/therapists/${id}`, { method: "DELETE" });
  },

  // --- ADMIN SHIFTS ---
  adminListShifts(
    params: { shop_id?: number; therapist_id?: number; date?: string; from?: string; to?: string },
    signal?: AbortSignal
  ) {
    const qs = new URLSearchParams();
    if (params.shop_id) qs.set("shop_id", String(params.shop_id));
    if (params.therapist_id) qs.set("therapist_id", String(params.therapist_id));
    if (params.date) qs.set("date", params.date);
    if (params.from) qs.set("from", params.from);
    if (params.to) qs.set("to", params.to);
    return request<AdminShift[]>(`/admin/shifts?${qs}`, { signal });
  },

  adminCreateShift(payload: unknown) {
    return request<AdminShift>("/admin/shifts", jsonPost(payload));
  },

  adminDeleteShift(id: number) {
    return request<void>(`/admin/shifts/${id}`, { method: "DELETE" });
  },

  // --- ADMIN NG LIST ---
  adminListNgList(signal?: AbortSignal) {
    return request<AdminNgItem[]>("/admin/ng-list", { signal });
  },

  adminAddNgList(payload: { phone: string; reason?: string }) {
    return request<AdminNgItem>("/admin/ng-list", jsonPost(payload));
  },

  adminDeleteNgList(id: number) {
    return request<void>(`/admin/ng-list/${id}`, { method: "DELETE" });
  },

  // --- ADMIN COURSES ---
  adminListCourses(shopId: number, includeInactive = false, signal?: AbortSignal) {
    return request<AdminCourse[]>(`/admin/courses?shop_id=${shopId}&include_inactive=${includeInactive}`, { signal });
  },

  adminCreateCourse(payload: unknown) {
    return request<AdminCourse>("/admin/courses", jsonPost(payload));
  },

  adminUpdateCourse(id: number, payload: unknown) {
    return request<AdminCourse>(`/admin/courses/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  adminDeleteCourse(id: number) {
    return request<void>(`/admin/courses/${id}`, { method: "DELETE" });
  },

  // --- ADMIN ADDONS ---
  adminListAddons(shopId: number, includeInactive = false, signal?: AbortSignal) {
    return request<AdminAddon[]>(`/admin/addons?shop_id=${shopId}&include_inactive=${includeInactive}`, { signal });
  },

  adminCreateAddon(payload: unknown) {
    return request<AdminAddon>("/admin/addons", jsonPost(payload));
  },

  adminUpdateAddon(id: number, payload: unknown) {
    return request<AdminAddon>(`/admin/addons/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  },

  adminDeleteAddon(id: number) {
    return request<void>(`/admin/addons/${id}`, { method: "DELETE" });
  },

  // --- ADMIN COMBO RESTRICTIONS ---
  adminListComboRestrictions(shopId: number, signal?: AbortSignal) {
    return request<AdminComboRestriction[]>(`/admin/combo-restrictions?shop_id=${shopId}`, { signal });
  },

  adminCreateComboRestriction(payload: { course_id: number; addon_id: number }) {
    return request<AdminComboRestriction>("/admin/combo-restrictions", jsonPost(payload));
  },

  adminDeleteComboRestriction(courseId: number, addonId: number) {
    return request<void>(`/admin/combo-restrictions?course_id=${courseId}&addon_id=${addonId}`, { method: "DELETE" });
  },
};
