# API Design (chi tiết) — BE Booking System
## Hệ thống booking massage — Flask (shop_api)

> REST + JSON, base path `/api/v1`. File này viết để **đưa thẳng vào prompt khi vibe code**: mỗi endpoint là một khối tự đủ (mục đích → auth → request → response → lỗi kèm message). Kết hợp với `openapi.yaml` (schema chính xác) và `erd-schema.sql` (DB).

---

## 0. Quy ước toàn hệ thống

### 0.1 Response lỗi — format duy nhất cho mọi API

```json
{
  "error": {
    "code": "SLOT_CONFLICT",
    "message": "Rất tiếc, khung giờ này vừa có người đặt. Vui lòng chọn giờ khác.",
    "details": { "suggested_slots": ["14:15", "14:30", "15:00"] }
  }
}
```

- `code`: hằng số để FE xử lý logic (switch-case)
- `message`: text hiển thị thẳng cho người dùng — BE là nguồn duy nhất của message, FE không tự bịa
- `details`: data phụ tùy loại lỗi (có thể null)

### 0.2 Catalog lỗi toàn hệ thống (message chuẩn — copy nguyên văn khi code)

| HTTP | code | BR | message (hiển thị cho user) | details |
|---|---|---|---|---|
| 400 | `VALIDATION_ERROR` | — | "Dữ liệu không hợp lệ, vui lòng kiểm tra lại." | `{fields: {tên_field: "lý do"}}` |
| 400 | `PARTY_SIZE_EXCEEDED` | BR-14 | "Mỗi lượt đặt tối đa 3 người. Nhóm đông hơn vui lòng liên hệ trực tiếp cửa hàng." | `{shop_phone}` |
| 400 | `ADDON_WITHOUT_COURSE` | BR-01 | "Dịch vụ bổ sung phải được đặt kèm một course chính." | — |
| 400 | `THERAPIST_NOT_ALLOWED` | BR-04 | "Đặt cho nhóm từ 2 người trở lên không thể chỉ định nhân viên." | — |
| 401 | `UNAUTHORIZED` | — | "Thông tin đăng nhập không đúng." (cố ý không nói rõ sai user hay password) | — |
| 401 | `EDIT_TOKEN_EXPIRED` | BR-17 | "Phiên chỉnh sửa nhanh đã hết hạn. Vui lòng dùng trang Quản lý đặt chỗ với mã đặt chỗ và email của bạn." | — |
| 403 | `FORBIDDEN` | — | "Bạn không có quyền thực hiện thao tác này." | — |
| 403 | `PHONE_BLOCKED` | BR-06 | "Số điện thoại này hiện không thể đặt chỗ online. Vui lòng liên hệ trực tiếp cửa hàng để được hỗ trợ." | `{reason, shop_phone}` (BR-20: có hiện lý do) |
| 404 | `BOOKING_NOT_FOUND` | — | "Không tìm thấy đặt chỗ. Vui lòng kiểm tra lại mã đặt chỗ và email." (sai cặp mã+email cũng trả lỗi này — không tiết lộ mã tồn tại) | — |
| 404 | `RESOURCE_NOT_FOUND` | — | "Không tìm thấy dữ liệu yêu cầu." | — |
| 409 | `SLOT_CONFLICT` | BR-08 | "Rất tiếc, khung giờ này vừa có người đặt trước. Bạn có thể chọn một trong các giờ gần nhất còn trống." | `{suggested_slots: [3 giờ gần nhất]}` |
| 422 | `INVALID_COMBO` | BR-09 | "Dịch vụ {addon_name} không thể đặt kèm {course_name}. Vui lòng chọn tổ hợp khác." | `{course_id, addon_id}` |
| 422 | `THERAPIST_OFF_SHIFT` | BR-05 | "Nhân viên {therapist_name} không làm việc trong khung giờ này. Bạn có thể đổi giờ khác hoặc bỏ chỉ định." | `{therapist_id}` |
| 422 | `MODIFY_DEADLINE_PASSED` | BR-16 | "Đã quá thời hạn thay đổi online (trước giờ hẹn 1 tiếng). Vui lòng gọi trực tiếp cửa hàng." | `{shop_phone}` |
| 422 | `SHOP_CHANGE_NOT_ALLOWED` | BR-18 | "Không thể đổi cửa hàng online. Vui lòng liên hệ cửa hàng để được hỗ trợ." | `{shop_phone}` |
| 500 | `INTERNAL_ERROR` | BR-12 | "Hệ thống đang gặp sự cố tạm thời. Vui lòng thử lại sau ít phút." | — |

> Lưu ý i18n: khách cuối là người Nhật — khi làm thật, `message` sẽ dịch sang tiếng Nhật hoặc trả message-key cho FE dịch. Giai đoạn dev dùng tiếng Việt cho dễ debug.

### 0.3 Xác thực — 3 cơ chế

| Cơ chế | Dùng cho | Cách hoạt động |
|---|---|---|
| Public | Luồng đặt chỗ (nhóm 1) | Không cần gì |
| `booking_code` + `email` | Khách quản lý booking (nhóm 2) | Gửi trong body, BE so khớp cặp (BR-15). Riêng sửa nhanh: header `X-Edit-Token` (JWT TTL 2 phút, BE cấp lúc tạo — BR-17) |
| JWT Bearer | Admin / Therapist (nhóm 3, 4) | `POST /auth/login` → token chứa `role`, `therapist_id?`. Therapist do admin cấp tài khoản |

### 0.4 Map màn wireframe ↔ API (thứ tự làm theo lát cắt dọc)

| Màn wireframe | API cần có | Ghi chú |
|---|---|---|
| 1. Chọn shop & ngày | `GET /shops` · `GET /shops/{id}/services?date=` | services trả `reason: SHOP_CLOSED` cho case A1 |
| 2. Số người & dịch vụ | (dùng lại data màn 1) | `restricted_course_ids` để disable add-on cấm |
| 3. Chọn giờ & therapist | `GET /shops/{id}/slots` · `GET /shops/{id}/therapists?date=` | endpoint therapists cho dropdown chỉ định theo tên |
| 4. Thông tin & xác nhận | `POST /customers/lookup` · `POST /bookings` | lookup chặn NG ngay khi nhập |
| 5. Hoàn tất + sửa 2' | `PATCH /bookings/{code}` (X-Edit-Token) | countdown từ `edit_token_expires_in` |
| 6. Tra cứu / sửa / hủy | `POST /bookings/retrieve` · `PATCH` · `POST .../cancel` | nút theo `can_modify` |
| 7. Đăng nhập | `POST /auth/login` | chung admin + therapist |
| 8. Trang Admin | `/admin/*` (3.1 → 3.6) | mỗi tab một cụm CRUD |
| 9. Trang Therapist | `GET /therapists/me/schedule?date=` | read-only |

---

## 1. Nhóm BOOKING-FLOW — luồng đặt chỗ của khách (public)

> Phục vụ UC-01 (đặt lịch, 12 bước) và UC-04 (tra slot). FE gọi lần lượt 1.1 → 1.5 theo stepper wireframe màn 1–5.

### 1.1 `GET /shops`
- **Mục đích:** lấy danh sách cửa hàng cho dropdown bước 1. Dữ liệu gần như tĩnh, FE có thể cache.
- **Response 200:** `[{id, shop_code, name, address, phone}]`
- **Lỗi:** chỉ có thể 500 `INTERNAL_ERROR`.

### 1.2 `GET /shops/{shopId}/services?date=&party_size=`
- **Mục đích:** lấy dịch vụ khả dụng của shop **theo ngày** (bước 4–5). Trả kèm thông tin combo cấm để FE disable add-on ngay khi khách chọn course (chặn sớm BR-09, đỡ 1 vòng lỗi).
- **Response 200:**
```json
{
  "courses": [{"id":3,"name":"もみほぐし","duration_min":60,"price":3980}],
  "addons":  [{"id":7,"name":"足つぼ","duration_min":15,"price":1000,"restricted_course_ids":[2]}],
  "reason": null
}
```
- Ngày shop nghỉ/thiếu nhân viên (case A1): vẫn 200 nhưng mảng rỗng + `"reason":"SHOP_CLOSED"` → FE hiện "Cửa hàng không phục vụ ngày này, vui lòng chọn ngày khác."
- **Lỗi:** 404 `RESOURCE_NOT_FOUND` (shopId sai), 400 `VALIDATION_ERROR` (date sai format).

### 1.3 `GET /shops/{shopId}/slots?date=&party_size=&course_id=&addon_ids=&therapist_id=&therapist_gender=`
- **Mục đích:** tính danh sách giờ bắt đầu khả dụng (bước 6, BR-07). **Chỉ mang tính hiển thị** — BE luôn re-check lúc tạo booking (BR-08), FE không được coi đây là "giữ chỗ".
- **Logic:** tổng thời lượng = course + addons → tìm therapist có shift phủ đủ (lọc theo chỉ định nếu có — BR-05) → mỗi khung 15 phút: đếm therapist rảnh (BR-03) → slot hợp lệ khi số therapist rảnh ≥ party_size.
- **Response 200:** `{"slots": ["10:00","10:15","14:00"]}` — rỗng = case A2, FE hiện "Ngày này đã kín chỗ."
- **Lỗi:** 400 `VALIDATION_ERROR` (thiếu tham số, party_size >3 → `PARTY_SIZE_EXCEEDED`), 404 `RESOURCE_NOT_FOUND`.

### 1.3b `GET /shops/{shopId}/therapists?date=` *(bổ sung — wireframe màn 3 cần)*
- **Mục đích:** danh sách therapist của shop cho dropdown "chỉ định theo tên" (bước 8, chỉ booking 1 người — BR-04). Lọc theo `date`: chỉ trả người **có ca ngày đó** để khách đỡ chọn nhầm người nghỉ.
- **Response 200:** `[{"id":5,"name":"Tanaka","gender":"female"}]`
- **Lỗi:** 404 `RESOURCE_NOT_FOUND` (shopId sai), 400 `VALIDATION_ERROR`.

### 1.4 `POST /customers/lookup`
- **Mục đích:** nhận dạng khách qua SĐT (bước 9 — UC-05) và chặn NG list ngay tại chỗ nhập (UC-06), không để khách đi tiếp rồi mới báo. Dùng POST (không phải GET) để SĐT không lộ trên URL/access log.
- **Request:** `{"phone": "09012345678"}`
- **Response 200:** `{"member_type":"member","rank":"Gold","visit_count":12}` — rank chỉ hiển thị (BR-20)
- **Lỗi:**

| HTTP | code | message |
|---|---|---|
| 403 | `PHONE_BLOCKED` | "Số điện thoại này hiện không thể đặt chỗ online. Vui lòng liên hệ trực tiếp cửa hàng." + `details.reason` |
| 400 | `VALIDATION_ERROR` | SĐT sai format |

### 1.5 `POST /bookings` ⭐ handler quan trọng nhất
- **Mục đích:** tạo booking (bước 11). Toàn bộ BR validate lại tại đây bất kể FE đã chặn — vì AI chatbot sau này không đi qua FE.
- **Header:** `Idempotency-Key` (uuid, optional) — chống bấm đúp tạo trùng.
- **Request:**
```json
{
  "shop_id": 1, "date": "2026-07-20", "start_time": "14:00",
  "party_size": 2, "phone": "09012345678", "email": "a@b.com",
  "course_id": 3,
  "reservations": [{"addon_ids":[7]}, {"addon_ids":[]}],
  "therapist_id": null, "therapist_gender": null
}
```
- **Thứ tự xử lý trong handler (giữ đúng thứ tự — lỗi rẻ check trước, transaction sau cùng):**
  1. Validate format (400 `VALIDATION_ERROR`)
  2. party_size 1–3 và khớp len(reservations) (400 `PARTY_SIZE_EXCEEDED`)
  3. Nhóm ≥2 → không được chỉ định therapist (400 `THERAPIST_NOT_ALLOWED`)
  4. course/addon tồn tại + is_active + cùng shop (404/422)
  5. Combo không nằm trong combo_restriction (422 `INVALID_COMBO`)
  6. Phone không trong ng_list (403 `PHONE_BLOCKED`)
  7. Therapist chỉ định có shift phủ giờ (422 `THERAPIST_OFF_SHIFT`)
  8. **Transaction:** lock slot/therapist (`SELECT ... FOR UPDATE`) → re-check đủ chỗ → không đủ: rollback + 409 `SLOT_CONFLICT` kèm `suggested_slots` → đủ: upsert customer, insert booking (confirmed) + reservations + reservation_addon → sinh `booking_code` = `{yyyyMMdd}-{shop_code}-{random 4-6 ký tự}` → commit
  9. Gửi email SES (async/sau commit — lỗi email không rollback)
- **Response 201:**
```json
{
  "booking_code": "20260720-S001-A1B2", "status": "confirmed",
  "edit_token": "eyJ...", "edit_token_expires_in": 120,
  "...": "echo lại thông tin booking"
}
```
- **Lỗi:** tất cả các mã ở bảng catalog trừ nhóm auth — cụ thể: 400 ×3, 403 `PHONE_BLOCKED`, 409 `SLOT_CONFLICT`, 422 ×2, 500.

---

## 2. Nhóm BOOKING-MANAGE — khách quản lý booking (UC-02, UC-03)

> Xác thực: `booking_code` + `email` trong body. Sai cặp → luôn 404 `BOOKING_NOT_FOUND` (không phân biệt "mã không tồn tại" vs "email sai" — tránh dò mã).

### 2.1 `POST /bookings/retrieve`
- **Mục đích:** tra booking để hiển thị màn quản lý (wireframe màn 6). Trả kèm `can_modify` BE tính sẵn theo deadline 1h (BR-16) — FE chỉ việc ẩn/hiện nút, không tự tính giờ.
- **Request:** `{"booking_code":"20260720-S001-A1B2", "email":"a@b.com"}`
- **Response 200:** chi tiết booking + `"can_modify": true`
- **Lỗi:** 404 `BOOKING_NOT_FOUND`.

### 2.2 `PATCH /bookings/{bookingCode}`
- **Mục đích:** sửa booking. Hai đường vào: (a) sửa nhanh ≤2 phút sau khi tạo bằng header `X-Edit-Token` (BR-17), (b) qua trang quản lý bằng `email` trong body (BR-15).
- **Được đổi (BR-18):** ngày/giờ, course, add-on, số người (≤3). **Không được:** đổi shop, nhóm >3.
- **Logic:** xác thực → check deadline 1h → validate như tạo mới → transaction: giải phóng slot cũ + giữ slot mới (cùng transaction) → email xác nhận thay đổi.
- **Lỗi:**

| HTTP | code | Khi nào |
|---|---|---|
| 401 | `EDIT_TOKEN_EXPIRED` | token quá 2 phút → FE chuyển hướng sang trang quản lý |
| 404 | `BOOKING_NOT_FOUND` | mã+email không khớp |
| 422 | `MODIFY_DEADLINE_PASSED` | còn <1h trước giờ hẹn |
| 422 | `SHOP_CHANGE_NOT_ALLOWED` | request chứa shop_id khác |
| 400 | `PARTY_SIZE_EXCEEDED` | sửa lên >3 người |
| 409 | `SLOT_CONFLICT` | giờ mới vừa bị chiếm — kèm suggested_slots |
| 422 | `INVALID_COMBO` / `THERAPIST_OFF_SHIFT` | như tạo mới |

### 2.3 `POST /bookings/{bookingCode}/cancel`
- **Mục đích:** hủy booking (UC-03). Dùng POST thay vì DELETE vì chỉ đổi status → `cancelled` + giải phóng slot, không xóa bản ghi.
- **Request:** `{"email":"a@b.com"}`
- **Response 200:** booking với `status: "cancelled"` + gửi email xác nhận hủy.
- **Lỗi:** 404 `BOOKING_NOT_FOUND`, 422 `MODIFY_DEADLINE_PASSED` (hủy cũng deadline 1h — BR-16).

---

## 3. Nhóm AUTH + ADMIN (UC-08 → UC-12, JWT role=admin)

> Admin quản lý **các cửa hàng** — API nhận `shop_id` làm tham số, dữ liệu giữa shop tách biệt (BR-11). Mọi endpoint nhóm này: thiếu/sai token → 401 `UNAUTHORIZED`; đúng token sai role → 403 `FORBIDDEN`.

### 3.0 `POST /auth/login`
- **Mục đích:** đăng nhập chung cho admin và therapist (tài khoản therapist do admin cấp).
- **Request:** `{"username","password"}` → **Response 200:** `{"access_token","role"}`
- **Lỗi:** 401 `UNAUTHORIZED` — message chung "Thông tin đăng nhập không đúng."

### 3.1 `GET/POST /admin/courses` + `PATCH/DELETE /admin/courses/{id}` (addon tương tự)
- **Mục đích:** CRUD dịch vụ; bật/tắt `is_active` để dịch vụ biến mất khỏi luồng đặt trong ngày shop thiếu người (US-06).
- **Lỗi đặc thù:** 400 `VALIDATION_ERROR` với `duration_min` không chia hết 15 — message: "Thời lượng phải là bội số của 15 phút." (BR-02)

### 3.2 `GET/POST/DELETE /admin/combo-restrictions`
- **Mục đích:** quản lý cặp course+addon bị cấm (BR-09). Bảng đang rỗng (mentor chưa có data) — bảng rỗng nghĩa là mọi combo hợp lệ.
- **Lỗi đặc thù:** 409 khi thêm cặp đã tồn tại — message: "Cặp dịch vụ này đã có trong danh sách cấm."

### 3.3 `GET/POST/PATCH/DELETE /admin/therapists`
- **Mục đích:** CRUD therapist. POST nhận optional `account: {username, password}` để cấp tài khoản đăng nhập luôn (đã chốt).
- **Lỗi đặc thù:** 409 `username` trùng — "Tên đăng nhập đã tồn tại."

### 3.4 `GET/POST/DELETE /admin/shifts`
- **Mục đích:** xếp ca cho therapist — nguồn dữ liệu cho thuật toán slot (BR-05).
- **Lỗi đặc thù:** 409 ca trùng (cùng therapist + ngày + giờ) — "Nhân viên đã có ca trùng khung giờ này."

### 3.5 `GET/POST/DELETE /admin/ng-list`
- **Mục đích:** quản lý SĐT bị cấm kèm `reason` (US-07). Reason sẽ hiển thị cho khách khi bị chặn (BR-20).

### 3.6 `GET /admin/bookings?shop_id=&date=&status=` + `PATCH /admin/bookings/{id}/status`
- **Mục đích:** xem booking của shop (UC-12); cập nhật sau phục vụ. PATCH `{"status":"completed"}` → **trong cùng transaction** +1 `visit_count` của khách (BR-19). `no_show` không cộng.
- **Lỗi đặc thù:** 422 khi chuyển trạng thái không hợp lệ (vd cancelled → completed) — "Không thể chuyển trạng thái này."

---

## 4. Nhóm THERAPIST (UC-11, JWT role=therapist)

### 4.1 `GET /therapists/me/schedule?date=`
- **Mục đích:** therapist xem ca + booking được gán cho mình theo ngày (wireframe màn 9). Read-only.
- **Bảo mật:** `therapist_id` lấy từ token — **không bao giờ** nhận từ query (tránh xem lịch người khác). SĐT khách che một phần: `090****5678`.
- **Response 200:** `{"shifts":[{start_time,end_time}], "bookings":[{start_time,duration_min,course_name,customer_phone_masked}]}`
- **Lỗi:** 401/403 như quy ước nhóm 3.

---

## 5. Quyết định thiết kế (trả lời khi được hỏi "sao làm vậy?")

1. **Race condition (BR-08):** không tin `GET /slots`; chốt chặn thật là lock trong transaction + re-check. 409 luôn kèm `suggested_slots` để FE xử lý case A6 không cần gọi thêm.
2. **Idempotency-Key** ở POST /bookings: khách bấm đúp/mạng lag không tạo 2 booking.
3. **Validate 2 tầng:** FE chặn sớm cho UX, BE validate lại toàn bộ — chatbot giai đoạn 2 không đi qua FE.
4. **Email qua Amazon SES**, gửi sau commit, lỗi thì retry — không rollback booking vì email.
5. **404 thay vì 403** khi sai mã+email: không tiết lộ mã đặt chỗ nào tồn tại (mã có format đoán được).
6. **Pragmatic REST — 3 điểm lệch chuẩn có chủ đích:** `POST /customers/lookup` và `POST /bookings/retrieve` (dữ liệu nhạy cảm không để trên URL/log), `POST .../cancel` (đổi trạng thái, không xóa — DELETE gây hiểu nhầm).
7. **Message lỗi do BE trả**, FE chỉ hiển thị — một nguồn duy nhất, sau này i18n sang tiếng Nhật chỉ sửa BE.

---

## 6. Checklist vibe-code theo thứ tự

- [ ] Error handler chung: bắt exception → format `{error: {code, message, details}}` (mục 0.1–0.2)
- [ ] `GET /shops` → `GET /services` → `GET /slots` (nhóm 1 đọc)
- [ ] `POST /customers/lookup` (có NG check)
- [ ] `POST /bookings` (transaction + lock — làm kỹ, viết test từng BR)
- [ ] `retrieve` / `PATCH` / `cancel` (nhóm 2)
- [ ] `POST /auth/login` + decorator `@require_role("admin")`
- [ ] CRUD admin (3.1 → 3.6) — copy pattern
- [ ] `GET /therapists/me/schedule`
