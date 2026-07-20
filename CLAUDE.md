# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo layout

| Đường dẫn | Vai trò |
|---|---|
| `shop_booking/shop_api/` | BE — Flask + SQLAlchemy + MySQL |
| `shop_booking/shop_web/` | FE — Next.js 16 App Router (repo git riêng) |
| `UC-US-BA-APIDESIGN/` | Nguồn sự thật nghiệp vụ: use case, business rule, ERD, API spec |
| `Giao diện/booking.dc.html` | Wireframe gốc — bảng màu và bố cục FE bám theo file này |
| `.venv/` | Virtualenv dùng chung, nằm ở gốc repo (KHÔNG ở trong `shop_api/`) |

Chỉ `shop_web/` là git repo; thư mục gốc thì không.

## Commands

**BE** — chạy từ `shop_booking/shop_api/` (thư mục chứa `.flaskenv` và `wsgi.py`; chạy chỗ khác sẽ báo "Could not locate a Flask application"):

```bash
docker compose up -d              # MySQL 8 ở cổng 3306
python -m flask run               # http://127.0.0.1:5000
python -m flask db upgrade        # áp migration
python -m flask db migrate -m "…" # sinh migration mới
```

**FE** — chạy từ `shop_booking/shop_web/`:

```bash
npm run dev        # http://localhost:3000
npm run build
npm run lint       # eslint
npx tsc --noEmit   # typecheck — luôn chạy cái này sau khi sửa .ts/.tsx
```

**Chưa có test nào trong repo** (không pytest, không jest/vitest). Cổng kiểm tra hiện tại chỉ là `tsc --noEmit` + `eslint` cho FE. Đừng bịa ra lệnh test.

### Hai điều dễ vấp trên máy này

- Dùng `python -m flask` / `python -m pip`, **không** gọi `flask.exe` hay `pip.exe` trong `.venv/Scripts/`. Windows Application Control chặn các shim `.exe` do pip sinh ra (chưa ký số) → `Program 'flask.exe' failed to run: An Application Control policy has blocked this file`. `python.exe` có chữ ký nên chạy được.
- `.venv` ở gốc repo, không nằm trong `shop_api/` — activate rồi mới `cd` vào `shop_api`.

## Kiến trúc

### FE ↔ BE nối bằng rewrite, không phải CORS

`next.config.ts` rewrite `/api/v1/:path*` → `http://127.0.0.1:5000/api/v1/:path*` (đổi bằng biến `API_TARGET`). Trình duyệt luôn gọi same-origin, nên **không bật CORS ở BE**. FE hardcode `BASE = "/api/v1"` trong `lib/api.ts` — đừng thay bằng URL tuyệt đối.

### BE

App factory `app/__init__.py` → `create_app()`. Một blueprint duy nhất `api_bp` (`url_prefix="/api/v1"`) trong `app/api/__init__.py`; các module route được import **sau** khi tạo blueprint để decorator `@api_bp.route` có chỗ gắn vào.

- `app/api/booking_flow.py` — luồng khách đặt chỗ (public): shops, services, slots, timeline, therapists, customers/lookup, POST /bookings
- `app/api/booking_manage.py` — khách tự tra/sửa/huỷ (có rate limit)
- `app/api/auth_admin.py` — login + toàn bộ CRUD admin
- `app/api/therapist.py` — `GET /therapists/me/schedule`
- `app/api/booking_helpers.py` — nơi đặt logic nghiệp vụ nặng: tính slot, kiểm tính khả dụng, validate BR, sinh mã đặt chỗ, edit token
- `app/models/shop.py` — **toàn bộ** model nằm trong một file này

`SECRET_KEY` được giải ngay lúc boot và **fail closed**: thiếu key mà `FLASK_DEBUG` không bật thì raise luôn, không đợi request đầu tiên. Key ngắn hơn 32 byte cũng bị chặn (HS256, RFC 7518 §3.2).

### Ba cơ chế xác thực

| Cơ chế | Dùng cho |
|---|---|
| Không cần gì | Luồng đặt chỗ của khách |
| `booking_code` + `email` trong body | Khách quản lý booking (BR-15). Riêng cửa sổ sửa nhanh 2 phút sau khi tạo: header `X-Edit-Token` (BR-17) |
| JWT Bearer | Admin / therapist — `@require_role(...)` trong `auth_admin.py` |

### Chống race khi tạo booking (BR-08)

`GET /slots` **không phải** chốt chặn. Chốt thật nằm trong `POST /bookings`: `SELECT … FOR UPDATE` trên toàn bộ therapist của shop → re-check khả dụng trong transaction → hết chỗ thì trả 409 `SLOT_CONFLICT` kèm `details.suggested_slots` để FE hiện luôn giờ thay thế, không phải gọi thêm API. Ngoài ra có chốt chặn bấm đúp: cùng khách + shop + ngày + giờ trong vòng 120 giây thì trả lại booking cũ thay vì tạo mới.

### Envelope lỗi — một format duy nhất

Mọi lỗi đều là `{"error": {"code", "message", "details?"}}`, dựng bởi `APIError` + `register_error_handlers` trong `app/api/errors.py`.

**Message lỗi do BE quyết, FE hiển thị nguyên văn** (quyết định thiết kế #7 — để sau này i18n sang tiếng Nhật chỉ sửa một chỗ). `lib/api.ts` bọc lại thành class `ApiError`; đừng viết message thay thế ở FE.

### FE

- **Fetch dữ liệu**: hook `useRequest(key, fetcher)` trong `lib/use-request.ts`. `key === null` = chưa đủ điều kiện, không gọi. Kết quả về muộn của key cũ bị bỏ qua theo `stamp` nên đổi lựa chọn nhanh không gây race. Dùng hook này thay vì tự `useEffect` + `fetch`.
- **Luồng đặt chỗ** là wizard 5 bước, toàn bộ state nằm ở `components/booking/booking-wizard.tsx`; các `step-*.tsx` chỉ nhận props. Cả 5 bước dùng chung khung `StepWindow` — bước 2 là một trang liền mạch (timeline + form trong cùng một cửa sổ), chia mục bằng `SectionBar` chứ không tách thành hai `Card`.
- **Bước 2 cố ý xếp ngược wireframe**: mục 1 "Chọn dịch vụ" (course/add-on/chỉ định) nằm TRÊN mục 2 "Chọn giờ" (timeline). Wireframe vẽ timeline trước rồi mũi tên "click slot ▼ mở form", nhưng `GET /slots` bắt buộc có `courseId` — chưa chọn course thì timeline rỗng, không thể là thứ đầu tiên khách thấy. Xếp theo wireframe thì khách phải cuộn xuống chọn course rồi lộn lên bấm slot. Đừng đảo lại.
- **Auth**: `lib/api.ts` → `authStorage` (localStorage) + `components/auth-guard.tsx`. AuthGuard đọc localStorage qua `useSyncExternalStore` và trả chuỗi thô — trả object đã parse sẽ re-render vô hạn.
- **UI primitives** dùng chung ở `components/ui.tsx` (`Card`, `WindowBar`, `Button`, `Chip`, `Field`, `Alert`, `Note`…). Cho `<Link>` trông như button thì dùng `buttonClass(variant)`, đừng lồng `<Link>` trong `<button>`.

### Bảng màu bám wireframe

`app/globals.css` khai báo CSS variable lấy **nguyên văn mã màu** từ `Giao diện/booking.dc.html`, rồi map sang Tailwind v4 qua `@theme inline`. Sửa số trong đó là lệch khỏi wireframe — đừng "làm đẹp" thêm. Dùng token (`bg-surface`, `border-frame`, `text-ink-2`…) chứ đừng viết mã hex trực tiếp.

## Business rules

Quy tắc nghiệp vụ đánh số `BR-01`…`BR-21`, định nghĩa trong `UC-US-BA-APIDESIGN/business-analysis-draft.md`. Code cả hai phía trích số hiệu này trong comment (`BR-04`, `BR-09`…) — khi đụng vào logic đặt chỗ, tra bảng đó trước. Vài cái hay gặp:

- **BR-04** nhóm ≥2 người không được chỉ định therapist
- **BR-09** một số tổ hợp course + add-on bị cấm (bảng `combo_restriction`)
- **BR-14** tối đa 3 người/booking
- **BR-16** sửa/huỷ chỉ được trước giờ hẹn ≥1 giờ
- **BR-17** token sửa nhanh TTL 2 phút cấp lúc tạo booking

Đặc tả endpoint chi tiết (params, response, mã lỗi từng case) ở `UC-US-BA-APIDESIGN/api-design.md`. Các case lỗi `A1`/`A2`/`A4`/`A6` mà comment FE nhắc tới cũng nằm trong bộ tài liệu này.

**Validate hai tầng**: FE chặn sớm cho UX nhưng BE validate lại toàn bộ — giai đoạn 2 sẽ có chatbot gọi thẳng API không qua FE. Đừng bỏ kiểm tra ở BE vì "FE chặn rồi".

## Quy ước viết code

- Comment và text hiển thị bằng **tiếng Việt**. Comment giải thích *tại sao*, không mô tả lại code.
- `shop_web/AGENTS.md` cảnh báo: đây là **Next.js 16**, có breaking change so với kiến thức sẵn có. Đọc guide trong `node_modules/next/dist/docs/` trước khi dùng API Next mà bạn không chắc.
- BE chưa có linter/formatter cấu hình sẵn. FE bắt buộc qua `eslint` + `tsc --noEmit` trước khi coi là xong.
