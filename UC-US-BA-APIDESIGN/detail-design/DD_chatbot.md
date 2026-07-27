# DD_chatbot — Detail Design: AI Chatbot đặt lịch (giai đoạn 2)

> Nguồn: `chatbot-architecture.md` (thiết kế mức cao) + `business-analysis-draft.md` (BR) + `api-design.md` (catalog lỗi §0.2/§0.2b, 7 quyết định) + `openapi.yaml` (schema) + `usecase-userstories-processflow.md` (UC/US/A1–A8).
> Mọi khẳng định nghiệp vụ dưới đây trích số hiệu; luật đổi thì chỉ cập nhật số hiệu.
> **Quy ước quan trọng:** chatbot là **client** của `shop_api` (giống FE), **không** chứa business logic. BE vẫn là chốt chặn cuối (validate 2 tầng — `api-design.md` quyết định #3). DD này mô tả *client* đó, không định nghĩa lại luật của BE.

> ⚠️ **CẬP NHẬT (mentor) — 2 thay đổi BE ở Mục 7.0 ĐÃ BỎ.** Q1 (`Idempotency-Key` + bảng `idempotency_key`) và Q2 (API key kênh `X-Api-Key` + bảng `channel_api_key`) **không còn**. Chatbot gọi API GĐ1 như **client public** (giống FE web) — **không thay đổi gì** ở `shop_api`. Chống bấm đúp do BE tự lo bằng **dedup thời gian 120s**. Mọi chỗ nhắc `X-Api-Key` / `Idempotency-Key` / hai bảng đó bên dưới coi như **không còn hiệu lực** (giữ lại để đọc lịch sử quyết định).

> ✅ **ĐỒNG BỘ CODE (2026-07-24).** DD đã được đối chiếu và cập nhật theo service thực tế tại `shop_booking/chatbot/` (Flask) + widget `shop_web/components/chat/chat-widget.tsx`. Ba điểm state machine khác nguồn `chatbot-architecture.md §3.1` (bỏ DURATION, tách COURSE↔ADDON, THERAPIST trước SLOT — §3.2). Những gì đã quyết ở Mục 7.0 nhưng code **chưa làm** được liệt kê ở **7.1 mục 6–10**.

---

## 1. Tổng quan & phạm vi

### 1.1 Module này là gì

Đây **không** phải một module trong `shop_api`/`shop_web`. Chatbot là **một service Flask riêng**, đã hiện thực tại `shop_booking/chatbot/` trong cùng repo (chạy độc lập, nói chuyện với `shop_api` qua HTTP như client public; `chatbot-architecture.md §8` từng dự kiến repo riêng — thực tế để cùng repo cho tiện). DD gom cả service này thành một lát cắt dọc vì các thành phần ràng buộc chặt với nhau (state machine ↔ error code ↔ tool API ↔ PII mask).

Service gồm các **sub-module** (mỗi cái ánh xạ 1 đơn vị code triển khai được — theo lộ trình MVP `chatbot-architecture.md §9`):

| Sub-module | File thực tế | Vai trò | Nguồn |
|---|---|---|---|
| `chat_widget` | `shop_web/components/chat/chat-widget.tsx` | Ô chat + render nút lựa chọn | §2, §7 |
| `web` | `app/main.py` | Flask app factory; endpoint đối ngoại duy nhất `POST /chat/message` + `GET /health` | §2.1 |
| `orchestrator` | `app/orchestrator.py` | Chạy vòng xử lý 6 bước cho mỗi lượt chat; nhánh CREATE/UPDATE/CANCEL/handoff | §1, §2 |
| `state_machine` | `app/state_machine.py` + `app/states.py` | **Code deterministic**: định nghĩa state, chọn state kế, merge entity, áp token nút | §3 |
| `nlu` | `app/nlu.py` | Bước ①: LLM trích param → JSON cố định (§3.4); kèm nhánh rule-based offline khi chưa cấu hình router | §3.4 |
| `nlg` | `app/nlg.py` + `app/templates.py` | Bước ⑤⑥: build prompt + LLM sinh câu; câu chứa số/mã thật dùng template code, **không** qua LLM (§10) | §1, §3.1 |
| `buttons` | `app/buttons.py` | Dựng nút lựa chọn theo state; `value` là token `key:value` đi đường tất định | §7 |
| `llm_client` | `app/llm_client.py` | Adapter router OpenAI-compatible; đổi provider = đổi `base_url`+`api_key` | §6.3 |
| `pii_masker` | `app/pii.py` + Vault | Che SĐT/email/mã đặt chỗ trước khi ra LLM (§6) | §6 |
| `session_store` | `app/session.py` | Mỗi `conversation_id`: state, slots, vault, lịch sử. **In-memory mặc định (dev); Redis khi có `REDIS_URL`** — cùng interface | §2 |
| `api_client` | `app/shop_api_client.py` | Gọi các endpoint giai đoạn 1 (§4). **Chỉ code gọi, LLM không gọi** | §2, §4 |
| `config` | `app/config.py` | Đọc env (`SHOP_API_BASE_URL`, `LLM_*`, `REDIS_URL`…); thiếu LLM/Redis vẫn chạy offline được | — |
| ~~*(BE thay đổi ①)*~~ | — | **ĐÃ BỎ** (API key kênh) — chatbot gọi API public | — |
| ~~*(BE thay đổi ②)*~~ | — | **ĐÃ BỎ** (Idempotency-Key) — dùng dedup 120s của BE | — |

### 1.2 UC/US phủ

- **UC-01** Đặt lịch (12 bước) — lõi. Chatbot dẫn khách đi đúng 12 bước bằng state machine (`chatbot-architecture.md §3.1` bám UC-01).
- **UC-04** Tra slot · **UC-05** Nhận dạng khách · **UC-06** Kiểm tra NG list · **UC-07** Chỉ định therapist — các include/extend của UC-01.
- **UC-02** Sửa · **UC-03** Hủy — "sửa/hủy trong phiên" (MVP §9 mục 8) — đã hiện thực bằng state `MODIFY`/`UPDATE`/`CANCELLED` (§3.5).
- **US-09** (AI chatbot là client dùng chung bộ API) — chính là US mà giai đoạn 1 đã thiết kế API hướng tới; hệ quả: "logic đặt 100% ở BE, FE/chatbot chỉ là client".

### 1.3 Cơ chế auth (client → `shop_api`) — `api-design.md §0.3`

Chatbot dùng **đúng 3 cơ chế như FE**, cộng một lớp kênh mới:

| Thao tác | Auth với `shop_api` |
|---|---|
| Luồng đặt chỗ (shops/services/slots/therapists/lookup/POST bookings) | **Public** |
| Sửa nhanh ≤2 phút sau khi tạo trong phiên | header `X-Edit-Token` (JWT TTL 2 phút — BR-17) |
| Sửa/hủy sau đó | `booking_code` + `email` trong body (BR-15) |
| ~~Toàn bộ request từ kênh chatbot~~ | ~~+ API key kênh `X-Api-Key`~~ — **ĐÃ BỎ**, gọi public như FE web |

### 1.4 Endpoint/hàm public module chịu trách nhiệm

- **Đối ngoại (widget → service):** `POST /chat/message` — endpoint hội thoại (schema đã chốt ở Mục 2.1 — Q3; **chưa thêm** vào `openapi.yaml` — 7.1). Kèm `GET /health` (báo đang chạy LLM router hay fake, Redis hay in-memory).
- **Hàm lõi (nội bộ):** `handle_turn(conversation_id, user_text, lang_hint) -> BotReply` (orchestrator), `next_state(session) -> str` + `merge_params(session, entities)` + `apply_button(session, token)` (state_machine), `extract(masked_text, lang, llm) -> dict|None` (nlu), `build_prompt(render_key, session, api_result, lang)` + `generate(prompt, llm)` (nlg), `mask / unmask / unmask_value / mask_response` (pii).

---

## 2. Interface & data contract

### 2.1 API đối ngoại — widget ↔ orchestrator (MỚI — schema đã chốt, Mục 7.0-Q3)

`chatbot-architecture.md` không đặc tả interface widget↔backend (chỉ vẽ ở §2). Chốt schema tối thiểu cho MVP — request/response đơn giản, **không** streaming (Mục 7.0-Q3):

`POST /chat/message`
```json
// request
{ "conversation_id": "uuid | null", "text": "string", "lang": "vi|en|ja | null" }
// response 200
{
  "conversation_id": "uuid",
  "reply_text": "string",           // câu bot đã sinh (NLG), ĐÃ unmask (PII của chính khách — xem ghi chú)
  "state": "COURSE",                // state hiện tại (debug/telemetry)
  "ui": { "buttons": [{"label":"もみほぐし 60'","value":"course:3"}] },  // §7 lựa chọn dạng nút
  "done": false                     // true khi vào DONE/CANCELLED/END/HUMAN
}
```
- `reply_text` là văn bản hiển thị cho **khách** → chứa PII thật của chính khách (vd đọc lại SĐT ở state CONFIRM) là hợp lệ vì đây là kênh của mình, **không** phải LLM. Việc unmask xảy ra khi ghép câu cuối (sau bước ⑥), trước khi trả widget.
- `conversation_id` = khóa Session Store. ~~Cũng là `Idempotency-Key` khi `POST /bookings`~~ — **đã bỏ** (banner đầu file); chống bấm đúp dựa vào dedup 120s của BE.
- `text` rỗng (mở widget) → màn chào cố định 3 thứ tiếng + nút chọn ngôn ngữ (`lang:vi|en|ja` → khóa `lang_locked`, ngừng tự đoán — §7). Câu chào nói rõ đây là trợ lý AI (minh bạch APPI).
- `text` dạng **token nút** `key:value` (`shop:1`, `slot:14:00`, `confirm:yes`, `modify:start`, `cancel:start`…) đi đường **tất định**: không qua NLU/LLM, không mask (giá trị đã là id/giờ, không phải câu nói).

### 2.2 Tool `shop_api` do State Machine gọi (bước ④) — không phải LLM

Tất cả trích thẳng `openapi.yaml`/`api-design.md`; **không** định nghĩa lại kiểu.

| State/thao tác | Method + path | Request (schema) | Response (schema) | Ghi chú |
|---|---|---|---|---|
| `SHOP` | `GET /shops` | — | `[{id, shop_code, name, address, phone}]` | render nút chọn shop (§4) |
| `COURSE` / `ADDON` | `GET /shops/{shopId}/services?date=&party_size=` | query | `{courses[], addons[{…, restricted_course_ids[]}], reason}` | hai state dùng chung endpoint; `restricted_course_ids` để ẩn add-on cấm khỏi nút (BR-09, A3 sớm); `reason:"SHOP_CLOSED"` = A1 |
| `SLOT` | `GET /shops/{shopId}/slots?date=&party_size=&course_id=&addon_ids=&therapist_id=&therapist_gender=` | query (openapi L65–90) | `{slots: ["14:00", …]}` | rỗng = A2. **Chỉ hiển thị**, BE re-check lúc tạo (BR-08). `addon_ids` = **hợp** add-on của mọi người (endpoint chỉ nhận 1 bộ; BE re-check riêng từng reservation lúc tạo). Giờ đã qua của HÔM NAY bị chatbot lọc bỏ |
| `THERAPIST` | `GET /shops/{shopId}/therapists?date=` | query | `{therapists: [{id, name, gender}]}` | **chỉ khi `party_size==1`** (BR-04), đứng **TRƯỚC** SLOT (§3.2). Chỉ lộ tên+giới tính; khách nêu tên ("Hana") thì map id xong tiến thẳng SLOT |
| `CONTACT` | `POST /customers/lookup` | `{phone}` | `CustomerInfo{member_type, rank, visit_count}` | chặn NG tại chỗ (BR-06) → 403 `PHONE_BLOCKED` |
| `CREATE` | `POST /bookings` | `BookingCreateRequest` (openapi L581) | `BookingCreated{…, edit_token, edit_token_expires_in}` | gọi public; ~~Idempotency-Key~~ **đã bỏ** — BE dedup 120s chống bấm đúp |
| sửa trong phiên | `PATCH /bookings/{bookingCode}` | `BookingUpdateRequest` | `Booking` | còn hạn 2': header `X-Edit-Token` (BR-17); hết hạn: **bỏ header**, thêm `email` vào body (BR-15 — BE ưu tiên token nên gửi token hết hạn sẽ 401) |
| hủy trong phiên | `POST /bookings/{bookingCode}/cancel` | `{email}` | `Booking{status:"cancelled"}` | idempotent (api-design 2.3); email thật lấy từ vault — vault đã rút thì hướng khách sang trang Quản lý đặt chỗ |
| tra lại | `POST /bookings/retrieve` | `{booking_code, email}` | `Booking{…, can_modify}` | client có sẵn hàm nhưng luồng hiện tại **chưa dùng** (hết token → PATCH thẳng với email, xem hàng trên) |

**Kiểu chính xác** (từ `openapi.yaml`, không chép sai):
- `BookingCreateRequest.required = [shop_id, date, start_time, party_size, phone, email, course_id, reservations]`; `party_size` int 1–3 **phải khớp** `len(reservations)`; `reservations[].addon_ids: int[]`; `therapist_id`/`therapist_gender` nullable, **loại trừ nhau**, chỉ khi 1 người.
- `BookingCreated = Booking + {edit_token: string, edit_token_expires_in: int=120}`.
- `Gender` enum (openapi) cho `therapist_gender`.

### 2.3 Hợp đồng đầu ra NLU (bước ①) — `chatbot-architecture.md §3.4`

LLM ở bước ① nhận instruction "chỉ trích xuất, không trả lời", trả **JSON cố định**:
```json
{
  "intent": "book|modify|cancel|ask_info|chitchat|handoff",
  "entities": {
    "date": "2026-07-23|null", "time": "08:00|null",
    "party_size": "1|null", "duration": "60|null",
    "course": "text|null", "addons": [],
    "therapist": "name|gender|none|null", "confirm": "yes|no|null"
  }
}
```
- Code **validate JSON này trước khi merge** (§3.4): `entities` sai kiểu → coi như không trích được → hỏi lại; riêng `intent` thiếu/lạ thì **mặc định `book`** để không rơi cả lượt. Router lỗi → fallback trích **rule-based offline** (đủ luồng chính, không thay LLM thật). Đây là ranh giới "LLM hiểu" ↔ "code quyết" và là chỗ chống prompt injection tầng client.
- `date` phải là ISO tuyệt đối — prompt cấp "Hôm nay" để LLM tự quy đổi; code còn lưới `_to_iso_date` (hôm nay/mai/ngày kia, vi+en+ja), không quy được thì bỏ (hỏi lại) — thà hỏi lại còn hơn để "tomorrow" lọt vào `slots.date`.
- `course`/`therapist` là **text/placeholder tự do** → phải map về `id` qua tool response, không tin nguyên văn (Mục 10 "NLU trích sai param").

### 2.4 Session Store (Redis) — schema MỚI (do DD định nghĩa, không có trong ERD)

Khóa `conversation_id` (chính sách Q5: TTL **sliding 30'** refresh mỗi lượt save, **rút vault** sau cửa sổ sửa nhanh 2' khi phiên đã kết thúc). Dev mặc định **in-memory**, Redis khi có `REDIS_URL` — cùng interface `SessionStore`. ⚠️ Mã hóa app-level field `vault` (Q5) **chưa implement** — `VAULT_ENC_KEY` mới dành chỗ trong config (7.1):
```
{
  "state": "COURSE", "lang": "vi",
  "lang_locked": false,                // khách đã CHỌN ngôn ngữ -> ngừng tự đoán (§7)
  "slots": {
    "shop_id":1, "date":"2026-07-23", "party_size":2, "duration":60,
    "course_id":3, "course_name":"Toàn thân",       // cache đọc lại ở CONFIRM, khỏi gọi API lần nữa
    "guest_addons":[[7],[]], "addon_guest_idx":0,   // add-on RIÊNG từng người (BR-10)
    "addons_decided":false,                         // add-on tùy chọn -> phải chốt tường minh mới rời ADDON
    "slot":"14:00", "wanted_time":"08:00",          // "giờ mong muốn" — ưu tiên gợi ý quanh giờ này (§3.3)
    "therapist_id":null, "therapist_gender":null, "therapist_decided":false,
    "phone":"{{phone_1}}", "email":"{{email_1}}",   // placeholder — unmask khi gọi API
    "contact_verified":false,                       // đã qua POST /customers/lookup (chặn NG — BR-06)
    "confirm":null,
    "course_text":null, "therapist_text":null,      // gợi ý tự do NLU — PHẢI map về id (§2.3)
    "party_over":false                              // khách nói >3 người -> nhánh handoff (BR-14)
  },
  "vault": { "{{phone_1}}":"…", "{{email_1}}":"…", "{{code_1}}":"…" },  // §6 — rút sau 2' khi phiên kết thúc (Q5)
  "booking_code": null, "edit_token": null, "edit_token_expires_at": null,
  "editing": false,                    // đang sửa lịch đã đặt (UC-02) -> đích là UPDATE thay vì CREATE
  "shop_phone": null,                  // cache cho handoff (A5/A8)
  "history": [ {role, masked_text} ],  // đã mask
  "turn_count": 0
}
```

### 2.5 PII Vault — `chatbot-architecture.md §6.2`

| Loại | Cách bắt | Placeholder |
|---|---|---|
| SĐT | regex ứng viên rộng (`+`, space, `-`, `.`) rồi lọc 9–15 chữ số — phủ VN/JP, **bắt rộng có chủ đích** (Q6) | `{{phone_N}}` |
| Email | regex | `{{email_N}}` |
| Mã đặt chỗ | regex `\d{8}-[A-Za-z0-9]+-[A-Za-z0-9]+` (format `{yyyyMMdd}-{shop_code}-{rand}` — api-design 1.5; đặt TRƯỚC phone để 8 số đầu không bị bắt nhầm) **+ so khớp nguyên văn** `booking_code` đã biết trong phiên (Q6) | `{{code_N}}` |
| Tên khách | **từ response API — không đưa vào context LLM** (strip cứng ở `mask_response`, không dựa regex) | — |

Cùng một giá trị xuất hiện lại → tái dùng placeholder cũ, không đẻ trùng. `mask_response` xóa `customer_name` mọi cấp và name/phone/email trong object `customer` (giữ `member_type/rank/visit_count`); tên shop/course/therapist là dữ liệu nghiệp vụ, được giữ.

### 2.6 Bảng/model đụng tới

- **Không** ghi thẳng DB của `shop_api`. Mọi đọc/ghi qua API → các bảng phía sau thuộc `shop_api` (`shop, course, addon, therapist, shift, customer, ng_list, booking, reservation, reservation_addon, combo_restriction` — `business-analysis-draft.md §1`). Chatbot **read qua API**, **write** duy nhất qua `POST/PATCH/cancel bookings`.
- **Persistence mới:** Session Store (in-memory/Redis, §2.4) + PII Vault (§2.5) thuộc service chatbot — ngoài DB quan hệ, **không cần migration**. ~~Bảng `channel_api_key` (Q2) và `idempotency_key→booking` (Q1) thuộc `shop_api`~~ — **đã bỏ** theo mentor (banner đầu file), không bổ sung gì vào `erd-schema.sql`.

### 2.7 Mã lỗi nhận từ `shop_api` (client xử lý) — catalog `api-design.md §0.2`

Chatbot **không sinh** mã lỗi nghiệp vụ; nó **nhận** từ BE và chọn nhánh + template. Bảng đầy đủ ở Mục 5. Ngoài ra kênh chatbot có thể gặp mã hạ tầng: **429** (rate limit nhóm 2 và login — api-design §2/§3.0, và rate limit kênh chatbot §8) → template "thử lại sau".

---

## 3. Pseudocode / thuật toán

### 3.1 Vòng xử lý 1 lượt (orchestrator) — `chatbot-architecture.md §1`

```
handle_turn(cid, user_text, lang_hint):
    session = session_store.load(cid) or Session(state=GREETING)
    if user_text rỗng: return greeting()               # màn chào 3 thứ tiếng + nút chọn ngôn ngữ, KHÔNG qua LLM

    if is_button_token(user_text):                     # token nút "key:value" — TẤT ĐỊNH
        signal = apply_button(session, user_text)      # không NLU, không mask (§3.3)
        signal == "handoff" | "modify_menu" | "cancel" -> nhánh riêng (HANDOFF / menu MODIFY / hủy UC-03)
    else:
        # ① NLU (LLM) — chỉ trích param; mask PII trước khi ra LLM
        masked = pii.mask(user_text, session.vault, extra=[booking_code])
        nlu = nlu.extract(masked, session.lang, llm)   # None (sai schema) -> REPROMPT (§3.4)
        if not session.lang_locked: session.lang = detect_lang(user_text) or session.lang   # §7
        if nlu.intent == "handoff": return go_handoff(session)   # MVP: chỉ nút [📞 Gọi cửa hàng] (Q9)
        # đã có booking_code mà khách nhắn tiếp: cancel/modify bằng lời -> nhánh UC-02/03
        capture phone/email placeholder từ vault vào slots   # lưới hứng Q6
        # ② MERGE — gộp entity đã trích vào slots (chỉ field không null)
        merge_params(session, nlu.entities)            # §3.3 (gồm xóa slot mâu thuẫn)

    if slots.party_over: return go_handoff(session)    # BR-14/A8 — không cần gọi BE

    # ③ STATE MACHINE (code) — chọn state kế
    session.state = next_state(session)                # §3.2
    # ④ VALIDATE + CALL API (code) — có thể ĐỔI state theo A1/A2/lỗi (§3.6)
    api_result = run_state_action(session)             # gọi tool §2.2

    # ⑤ BUILD PROMPT + ⑥ NLG (LLM hoặc template offline)
    reply = nlg.generate(nlg.build_prompt(render_key, session, api_result, session.lang))
    reply = pii.unmask(reply, session.vault)           # trả PII thật của chính khách cho widget
    session.maybe_drop_vault(); session_store.save(session)   # Q5: rút vault sau 2'
    return BotReply(reply, ui=buttons_for(...), done=is_terminal(session.state))
```
- **LLM chỉ ở ① và ⑥.** Bước ②③④⑤ là code thuần → unit-test không cần LLM (§9 mẹo test, Mục 6).
- Câu chứa **số/mã thật** (SLOT/DONE/UPDATED/CANCELLED/END/HANDOFF/ERROR) **không qua LLM** — dùng template code để LLM không có cơ hội sửa/bịa số (§10); template đã tách vi/en/ja nên vẫn đủ đa ngôn ngữ.

### 3.2 Hàm chọn state kế (bước ③) — `chatbot-architecture.md §3.1–3.2`

Thứ tự state (điều kiện vào + slot phải có để rời) — **khác nguồn `chatbot-architecture.md §3.1` ba điểm** (code đã chốt, xem `states.py`): ① **bỏ `DURATION`** (course đã kèm sẵn thời lượng), ② **tách `SERVICE` → `COURSE` + `ADDON`** (add-on hỏi RIÊNG từng người — BR-10), ③ **`THERAPIST` đứng TRƯỚC `SLOT`** (chỉ định người trước để `GET /slots` lọc đúng giờ trống của người đó — khách chỉ thấy giờ họ thực sự rảnh):

`GREETING → SHOP(shop_id) → DATE(date) → PARTY_SIZE(1–3) → COURSE(course_id) → ADDON(addons_decided) → THERAPIST(therapist_decided, chỉ khi party_size==1) → SLOT(slot) → CONTACT(phone+email+contact_verified) → CONFIRM(confirm==yes) → CREATE|UPDATE → DONE`

Ngoài vòng hỏi còn: `UPDATE` (đang sửa — UC-02), `CANCELLED` (đã hủy — UC-03), `MODIFY` (menu "đổi gì" — chỉ render+nút), `END` (A5), `HUMAN` (phase sau — Q9). Terminal = `{DONE, CANCELLED, END, HUMAN}`.

```
next_state(session):
    for st in STATE_ORDER:                 # GREETING…CONFIRM
        if not entry_condition(st, session): continue     # vd THERAPIST khi party_size>1 (BR-04), ADDON khi chưa có course (BR-01)
        if not slots_satisfied(st, session): return st     # state đầu tiên còn thiếu slot
    return UPDATE if editing else (DONE if booking_code else CREATE)
```
- Khách nói gộp *"mai 2 người"* → merge lấp `date,party_size` một lượt → nhảy thẳng `COURSE`, không hỏi lại từng câu.
- Rời `CONTACT` đòi cả **`contact_verified`** (đã qua `POST /customers/lookup` chặn NG — BR-06), không chỉ "có phone/email".
- Rời `ADDON` đòi **chốt tường minh** (`addons_decided` — nút "Không thêm"/"Xong") vì add-on là tùy chọn, không suy được từ dữ liệu.

### 3.3 Merge param + vô hiệu hóa slot mâu thuẫn (bước ②) — `chatbot-architecture.md §3.2`

```
merge_params(session, ent):                # chỉ field không null
    time -> slots.wanted_time              # "giờ mong muốn" — KHÔNG tính là đổi slot
    party_size > 3 -> slots.party_over = True (KHÔNG set party_size)   # BR-14 -> nhánh handoff
    course (text) -> slots.course_text     # gợi ý — map về id qua GET /services (§2.3); đổi course giữa chừng -> course_id=None, map lại
    therapist: "none/skip" -> bỏ chỉ định; "male/female" -> gender (chỉ khi party_size==1 — BR-04);
               tên -> slots.therapist_text (map id qua GET /therapists)
    confirm "yes|no" -> slots.confirm

_invalidate(changed):                      # XÓA slot không còn chắc hợp lệ (dùng chung với apply_button)
    party_size>1               -> xóa therapist_* (BR-04)
    course_id | party_size     -> reset guest_addons + addons_decided   # cấu trúc add-on đổi (BR-09/BR-10)
    course_id | party_size | date -> slot=None, confirm=None            # BR-07 + đổi đơn thì xác nhận lại
```
- *"à cho 3 người"* giữa chừng → `party_size=3`, therapist bị **xóa** (BR-04), add-on chọn lại từ đầu, state quay về nhánh phù hợp.
- Token nút đi qua `apply_button` với cùng bộ `_invalidate`; riêng nút `therapist:*` luôn xóa `slot`+`confirm` (giờ trống phụ thuộc người phục vụ).

### 3.4 PII mask / unmask (bước bao quanh ①⑥) — `chatbot-architecture.md §6.1`

```
mask(text, vault):   regex bắt phone/email/code → tạo {{phone_N}}… lưu vault → thay vào text
unmask(reply, vault): thay placeholder trong câu bot bằng giá trị thật (chỉ cho widget của khách)
before_call_api: state machine thay placeholder → giá trị thật khi build BookingCreateRequest
after_api:  mask_response(obj) trước khi bất kỳ field nào lọt vào context LLM (tên khách bỏ hẳn)
```
- **Thu PII (Q6):** khách gõ phone/email trong chat → regex `mask()` nạp vào vault, orchestrator gắn placeholder vào `slots.phone/email` (`_capture_contact_from_vault`); regex **lệch bắt rộng** (mask thừa hơn sót). ⚠️ **Form field widget** (thu tất định ở CONTACT theo Q6) **chưa có** — hiện lưới regex là đường duy nhất (7.1 mục 7).
- **Mã đặt chỗ (Q6):** mask bằng **cả** regex `\d{8}-[A-Za-z0-9]+-[A-Za-z0-9]+` **lẫn so khớp giá trị thật** trong vault khi session đã có `booking_code` (regex code chạy TRƯỚC phone để 8 số đầu không bị bắt nhầm).
- **Tên khách (Q6):** quy tắc cứng — `mask_response()` strip field tên trước khi bất kỳ thứ gì vào prompt, không dựa regex.
- Kết quả: nhà cung cấp LLM chỉ thấy *ý định*, không nhận PII (§6, quyết định 2 & 4).

### 3.5 State CREATE — `POST /bookings` (bước ④ tại CONFIRM=yes)

```
on CREATE:
    if confirm != "yes": state = CONFIRM; return   # guardrail 1 (§4.2): không ghi khi chưa xác nhận
    reservations = [{addon_ids: guest_addons[i]} for i in range(party_size)]   # BR-10: add-on RIÊNG từng người
    body = BookingCreateRequest(slots, phone/email unmask từ vault)
    resp = api.post_bookings(body)                 # KHÔNG header gì thêm — BE dedup 120s chống bấm đúp (Q1 đã bỏ)
    if 201:
        session.booking_code, session.edit_token = resp.booking_code, resp.edit_token
        session.edit_token_expires_at = now + resp.edit_token_expires_in   # =120s (BR-17)
        state = DONE  → template "thành công + mã đã gửi email" (BR-15) + nút [✏️ Sửa lịch][🗑 Hủy lịch]
    else: handle_api_error(resp.error.code)        # §3.6
```
- **Không** tự sinh `booking_code` — BE sinh (BR-12). **Không** rollback vì email; email do BE gửi sau commit qua SES (api-design quyết định #4).

**UPDATE / CANCEL trong phiên (UC-02/03):**
```
on UPDATE:   # editing=True, đủ slot và confirm==yes (đọc lại đơn mới rồi mới ghi)
    edit_token còn hạn -> PATCH /bookings/{code} + X-Edit-Token           (BR-17)
    hết hạn            -> PATCH với email trong body, KHÔNG gửi token     (BR-15; không qua /bookings/retrieve)
    vault đã rút (mất email thật) -> báo "vào trang Quản lý đặt chỗ với mã + email"
    thành công -> editing=False, state=DONE, template UPDATED

on CANCEL:   # nút cancel:start hoặc intent "cancel" khi đã có booking_code
    email thật từ vault -> POST /bookings/{code}/cancel {email}
    vault đã rút -> báo sang trang Quản lý đặt chỗ
    thành công -> state=CANCELLED
```
- Menu `MODIFY` (nút "Sửa lịch") cho chọn đổi giờ / số người / dịch vụ — mỗi lựa chọn xóa đúng nhóm slot tương ứng rồi state machine tự quay lại bước cần hỏi.

### 3.6 Map error.code → nhánh state (bước ⑤⑥ khi API lỗi) — `chatbot-architecture.md §4.1`

```
handle_api_error(code):
    SLOT_CONFLICT        -> state=SLOT (xóa slot+confirm); đọc details.suggested_slots; template A6
    PHONE_BLOCKED        -> state=END;   template A5 (message BE + details.shop_phone)
    THERAPIST_OFF_SHIFT  -> state=THERAPIST (mở lại lựa chọn); "đổi giờ hay bỏ chỉ định?" (A4)
    INVALID_COMBO        -> state=ADDON; gỡ add-on gây cấm (details.addon_id) khỏi MỌI người; template A3
    PARTY_SIZE_EXCEEDED  -> reset party_size; template HANDOFF (shop_phone) — A8/BR-14
    ADDON_WITHOUT_COURSE -> state=COURSE, xóa course_id; "cần chọn course chính" (BR-01)
    THERAPIST_NOT_ALLOWED-> xóa therapist, state=CONTACT; (BR-04)
    MODIFY_DEADLINE_PASSED / EDIT_TOKEN_EXPIRED / SHOP_CHANGE_NOT_ALLOWED
                         -> thông báo message BE (+shop_phone nếu có); (BR-16/BR-17/BR-18)
    VALIDATION_ERROR     -> REPROMPT chung (details.fields CHƯA được đọc — 7.1 mục 9)
    INTERNAL_ERROR / 429 -> giữ state, mời thử lại; (A7/BR-12)
```

---

## 4. Business rules enforcement

> **Chatbot KHÔNG enforce BR — chatbot chỉ hỏi/hiển thị/lọc sớm cho UX. Chốt chặn thật là `shop_api`** (validate 2 tầng — `api-design.md` quyết định #3; `chatbot-architecture.md §4.2` guardrail 4). Cột "vai trò client" nói rõ chatbot làm gì; cột "BE enforce ở" là nơi luật thực sự chạy.

| BR | Vai trò client (chatbot) | BE enforce ở | Mã lỗi nếu vi phạm |
|---|---|---|---|
| BR-14 (≤3 người) | state PARTY_SIZE giới hạn 1–3; >3 → nhánh handoff (A8) | `POST /bookings` bước 2 | 400 `PARTY_SIZE_EXCEEDED` |
| BR-04 (nhóm không chỉ định) | bỏ qua state THERAPIST khi `party_size>1`; xóa therapist khi đổi lên nhóm (§3.3) | `POST /bookings` bước 3 | 400 `THERAPIST_NOT_ALLOWED` |
| BR-01 (add-on phải kèm course) | state COURSE đứng trước ADDON; ADDON chỉ vào được khi đã có `course_id` (entry condition) | `POST /bookings` (payload chỉ add-on) | 400 `ADDON_WITHOUT_COURSE` |
| BR-09 (combo cấm) | ẩn add-on cấm khỏi nút theo `restricted_course_ids` (`buttons.py` — A3 sớm) | `POST /bookings` bước 5 | 422 `INVALID_COMBO` |
| BR-06 (NG list) | `POST /customers/lookup` chặn ngay ở state CONTACT (A5) | lookup + `POST /bookings` bước 6 | 403 `PHONE_BLOCKED` |
| BR-05 (therapist có ca) | chỉ liệt kê người có ca (`GET /therapists?date=`) | `POST /bookings` bước 7 | 422 `THERAPIST_OFF_SHIFT` |
| BR-07 (slot phụ thuộc điều kiện) | gọi `GET /slots` với đủ tham số; đổi điều kiện → xóa slot cũ (§3.3) | thuật toán slot + re-check | — / 409 |
| BR-08 (slot hết realtime) | không tin `GET /slots`; xử lý 409 → gợi ý `suggested_slots` (A6) | `POST /bookings` transaction | 409 `SLOT_CONFLICT` |
| BR-10 (nhóm cùng course) | state COURSE chọn **một** course cho cả nhóm; state ADDON hỏi **riêng từng người** (`guest_addons` → `reservations[].addon_ids`) | `POST /bookings` (course_id + reservations[]) | 400/422 |
| BR-02 (bội số 15') | chỉ chào các gói hợp lệ do API trả (không tự bịa — Mục 10); đã bỏ state DURATION — course quyết thời lượng | admin/service data | 400 `VALIDATION_ERROR` |
| BR-15 (email nhận mã) | state CONTACT thu email; DONE báo "mã gửi email" | `POST /bookings` | — |
| BR-17 (edit token 2') | lưu `edit_token`; sửa nhanh dùng `X-Edit-Token` | `PATCH` header | 401 `EDIT_TOKEN_EXPIRED` |
| BR-16 (deadline 1h) | với sửa/hủy trong phiên, đọc `can_modify` | `PATCH`/`cancel` | 422 `MODIFY_DEADLINE_PASSED` |
| BR-18 (không đổi shop) | không cho đổi shop trong phiên sửa | `PATCH` | 422 `SHOP_CHANGE_NOT_ALLOWED` |

> Vì chatbot **không đi qua FE**, chính nó là "client thứ hai" mà `api-design.md §0.2` đã lường trước: mã `ADDON_WITHOUT_COURSE` (BR-01) "không phát sinh qua FE nhưng giữ cho chatbot" là ví dụ trực tiếp — nếu NLU chỉ trích add-on mà thiếu course, BE bắt lỗi này.

---

## 5. Alternative & exception flows

Map A1–A8 (`usecase-userstories-processflow.md §2`) + mã lỗi → hành vi chatbot. **A1/A2 không phải lỗi** (BE trả 200 với mảng rỗng) — chatbot phải tự nhận biết.

| Nhánh | Tình huống | Tín hiệu từ BE | Hành vi chatbot |
|---|---|---|---|
| **A1** | Ngày shop nghỉ/thiếu người (bước 6) | `GET /services` → 200, `reason:"SHOP_CLOSED"`, mảng rỗng | quay state DATE, template "cửa hàng không phục vụ ngày này, chọn ngày khác" |
| **A2** | Ngày hết slot (bước 6) | `GET /slots` → 200 `{slots:[]}` (hoặc rỗng sau khi lọc giờ đã qua của hôm nay) | không chỉ định ai: quay DATE — "ngày này đã kín chỗ"; **đang chỉ định therapist**: quay THERAPIST — "người này kín lịch: đổi người / để shop sắp / đổi ngày?" |
| **A3** | Combo course+add-on cấm (bước 5) | 422 `INVALID_COMBO` `{course_id,addon_id}` | quay state ADDON, **gỡ add-on gây cấm** khỏi mọi người, đọc `message` BE |
| **A4** | Therapist không có ca (bước 8) | 422 `THERAPIST_OFF_SHIFT` `{therapist_id}` | quay THERAPIST/SLOT: "đổi giờ hay bỏ chỉ định?" |
| **A5** | SĐT trong NG list (bước 9) | 403 `PHONE_BLOCKED` `{reason, shop_phone}` | state END: đọc `message`+`reason`, đưa `shop_phone`; **không** tạo booking |
| **A6** | Slot vừa bị chiếm (bước 11) | 409 `SLOT_CONFLICT` `{suggested_slots:[3 giờ]}` | quay SLOT, **hiện luôn** `suggested_slots` (không gọi lại `GET /slots`) |
| **A7** | Lỗi hệ thống (bước 11) | 500 `INTERNAL_ERROR` | giữ state CONFIRM, mời thử lại |
| **A8** | Nhóm >3 (bước 3) | 400 `PARTY_SIZE_EXCEEDED` `{shop_phone}` | nhánh handoff, "tối đa 3 người/lượt", đưa `shop_phone` |

Nhánh sửa/hủy (UC-02/03) ngoài A1–A8:
- Edit token hết 2 phút → **không gửi token nữa**, `PATCH` với `email` trong body (BR-15; BE ưu tiên token nên gửi token hết hạn sẽ 401 `EDIT_TOKEN_EXPIRED`). Vault đã rút (mất email thật) → hướng khách sang trang Quản lý đặt chỗ với mã + email. `/bookings/retrieve` có trong client nhưng luồng hiện tại **chưa dùng**.
- Còn <1h → 422 `MODIFY_DEADLINE_PASSED` → đưa `shop_phone` (BR-16).
- Đòi đổi shop → 422 `SHOP_CHANGE_NOT_ALLOWED` (BR-18).

**Guardrails** (`chatbot-architecture.md §4.2`): (1) `POST /bookings` chỉ chạy khi state=CONFIRM & khách đồng ý; (2) LLM không có quyền gọi API — dù NLU trả param lạ, code vẫn đi theo bảng chuyển state; (3) bot không dùng PII ngoài đơn hiện tại (quyết định 4); (4) BE validate lại toàn bộ → chống prompt injection.

---

## 6. Test scenarios (acceptance)

> Test offline **đã hiện thực** tại `chatbot/tests/test_chatbot.py` — chạy `python tests/test_chatbot.py` từ thư mục `chatbot/`, không cần pytest/LLM/Redis/shop_api thật (`StubApi` giả API, LLM để `None` → nhánh rule-based/template). **Mẹo (`chatbot-architecture.md §9`):** bước ③④⑤ là code → assert state kế + tool được gọi, LLM ở ①⑥ mock/fake.

**State machine (không cần LLM):**
- **T1 (nói gộp, §3.2):** *Given* slots rỗng (đã có shop) · *When* NLU trả `{date, party_size:2}` · *Then* `next_state==COURSE` (đã bỏ DURATION), **không** hỏi lại từng câu.
- **T2 (BR-04, §3.3):** *Given* `party_size:1, therapist_id:5` · *When* merge `party_size:3` · *Then* `therapist_id==None`, state không vào THERAPIST.
- **T3 (BR-14/A8):** *Given* NLU `party_size:5` · *When* chọn state · *Then* nhánh handoff; nếu vẫn gửi BE → 400 `PARTY_SIZE_EXCEEDED`.

**Luồng đầy đủ (mock LLM):** — bám AC của US
- **T4 (US-01 AC2):** đặt 1 người thành công → nhận `booking_code` + câu "đã gửi email".
- **T5 (US-01 AC3 / A6 / BR-08):** `POST /bookings` → 409 · *Then* bot đọc `suggested_slots`, quay SLOT, không gọi thêm API.
- **T6 (US-02 AC2 / A4):** chỉ định therapist nghỉ ca → 422 `THERAPIST_OFF_SHIFT` · *Then* bot hỏi "đổi giờ hay bỏ chỉ định".
- **T7 (US-03 AC1 / BR-10):** nhóm 3 người, 1 course, add-on riêng → tạo booking 3 reservation cùng giờ.
- **T8 (A5 / BR-06):** SĐT NG → `POST /customers/lookup` 403 `PHONE_BLOCKED` · *Then* state END, đưa `shop_phone`, **không** POST bookings.
- **T9 (A3 / BR-09):** add-on cấm → 422 `INVALID_COMBO` · *Then* quay ADDON, add-on gây cấm bị gỡ khỏi mọi người.
- **T10 (A1):** ngày nghỉ → `GET /services` `reason:SHOP_CLOSED` · *Then* quay DATE.
- **T11 (BR-17):** sửa trong 2' bằng `X-Edit-Token`; sau 2' → `PATCH` với `email` trong body (không gửi token hết hạn); vault đã rút → hướng sang trang Quản lý đặt chỗ.

**PII (§6 — unit test regex, MVP mục 2):**
- **T12:** input "SĐT 0901234567, email a@b.com" · *Then* text ra LLM chỉ có `{{phone_1}}`/`{{email_1}}`; body `POST /bookings` chứa giá trị thật; context LLM sau lookup **không** chứa tên khách.
- **T13:** mã đặt chỗ `20260720-S001-A1B2` trong tin nhắn → match regex `\d{8}-[A-Za-z0-9]+-[A-Za-z0-9]+` → `{{code_1}}`; mã đã biết trong phiên còn được so khớp nguyên văn (Q6).

**Đa ngôn ngữ / handoff (§7):**
- **T14:** tin nhắn tiếng Nhật → `lang=ja` lưu session → mọi NLG truyền `ja`; gọi API vẫn bằng `id`. Khách đã chọn ngôn ngữ bằng nút (`lang_locked`) thì **không** tự đoán nữa; email/SĐT/placeholder bị loại khỏi text trước khi đoán (chữ Latin trong email không phải tín hiệu tiếng Anh).
- **T15:** intent handoff → MVP hiện nút `[📞 Gọi cửa hàng]` (Q9). *(Phase sau: state HUMAN, bot ngừng tự trả lời, nút `[💬 Chat nhân viên]` hiện khi có admin online — presence, Q8.)*

---

## 7. Open questions & rủi ro

### 7.0 Quyết định đã chốt (buổi rà soát 2026-07-23)

| # | Vấn đề | Quyết định | Kéo theo (việc phải làm) |
|---|---|---|---|
| ~~**Q1**~~ **(ĐÃ BỎ)** | Chống tạo booking trùng | ❌ **Không** thêm `Idempotency-Key`/bảng nữa (mentor: chatbot gọi API bình thường) | Dùng **dedup thời gian 120s** sẵn có của BE. Chấp nhận hạn chế: 2 request đổi nội dung cùng giờ trong 120s bị gộp — hiếm với luồng chatbot có xác nhận |
| ~~**Q2**~~ **(ĐÃ BỎ)** | Auth kênh chatbot → `shop_api` | ❌ **Không** thêm API key kênh (mentor: chatbot gọi API như client public) | Bỏ bảng `channel_api_key` + middleware + mã lỗi `CHANNEL_UNAUTHORIZED`. Luồng đặt chỗ vốn public — chatbot dùng chung |
| **Q3** | Interface `/chat/message` (§2.1) | **Request/response đơn giản**, không streaming: `{conversation_id, text, lang} → {conversation_id, reply_text, state, ui.buttons[], done}` | ✅ Đã implement đúng schema (`main.py`). Nâng lên SSE sau **không phá** schema (chỉ đổi content-type). ❌ Chưa thêm vào `openapi.yaml` (7.1) |
| **Q4** | Cách trích entity NLU (§11.4) | **Tự viết prompt + validate JSON** (§3.4); **không** dùng function-calling riêng của model, **không** framework agent | Chạy đồng nhất trên mọi model qua router (hợp mục tiêu thử nhiều model cho tiếng Nhật — §10). Lưới "sai schema → hỏi lại" (§3.4) bắt lỗi format. Chuyển sang structured output sau này rất nhẹ (ranh giới "LLM trả JSON → code validate" không đổi) |
| **Q5** | Session Store & PII Vault (§2.4/2.5) | TTL **sliding 30'** (refresh mỗi lượt) + **rút vault** ngay sau cửa sổ sửa nhanh 2' (BR-17); **mã hóa app-level** riêng field `vault` (key env/KMS); **một Redis** cho MVP | ✅ TTL sliding + rút vault đã implement (`session.py`; dev dùng in-memory, Redis qua `REDIS_URL`; rút vault phủ cả `CANCELLED` — fix 2026-07-24). ❌ Mã hóa vault chưa làm (7.1) |
| **Q6** | Độ phủ regex PII (§6.2) | Nguyên tắc **mask thừa hơn sót**; phủ SĐT VN+JP rộng (mã vùng, separator, `0120`); **thu phone/email qua form field ở CONTACT** (tất định — regex chỉ là lưới hứng); mã đặt chỗ mask bằng **cả** regex **lẫn giá trị thật** trong vault; **tên khách không bao giờ vào context** (strip ở `mask_response`) | ✅ Regex + so khớp nguyên văn + strip tên đã implement (`pii.py` + test corpus). ❌ Form field ở widget chưa có — hiện thu phone/email hoàn toàn qua lưới regex (7.1 mục 7). Presidio = nợ production (7.1) |
| **Q7** | Region LLM production (§6.4) | MVP = **router + masking**; production **hướng Bedrock/Azure Tokyo** (data JP, có DPA, hợp APPI) | Đổi provider chỉ sửa `base_url`+`api_key` (adapter §6.3). **Quyết định cuối chờ mentor/khách** — treo ở 7.1 |
| **Q8** | Ai trực chat nhân viên (§11.b) | **Tài khoản admin** trực (JWT `role=admin` sẵn có); màn "Hộp thư hỗ trợ" nằm trong khu admin `app/admin/*`; nút `[💬]` hiện theo **presence** (có admin online) | Không cần auth mới, không cần định nghĩa "giờ trực" cố định — có admin online thì hiện nút |
| **Q9** | Phân kỳ handoff (§11.c, §7 nguồn, MVP §9.9) | **MVP chỉ nút `[📞 Gọi cửa hàng]`** (đưa `shop_phone`); state `HUMAN` + chat admin + màn hộp thư **dời phase sau** | Điều kiện tiên quyết phase sau = **wireframe màn hộp thư admin** (chưa có) — không còn chờ quyết định nhân sự (đã có Q8). Treo ở 7.1 |

> **Hệ quả tổng (sau cập nhật mentor):** phía BE **không thay đổi gì** — Q1/Q2 đã bỏ, chatbot gọi API GĐ1 như client public, chống bấm đúp dựa vào dedup 120s sẵn có của BE. ~~(Bản trước khi mentor bỏ: 2 thay đổi BE — API key kênh + Idempotency-Key — từng được đặc tả ở `api-design.md §7` / `openapi.yaml` / `erd-schema.sql`; giữ câu này để đọc lịch sử.)~~

### 7.1 Dời phase sau / chờ mentor (không chặn MVP)

1. **Region LLM production (Q7):** chốt cuối dùng router hay Bedrock/Azure Tokyo — **chờ mentor/khách duyệt**. Hướng đã có, adapter `llm_client.py` sẵn để đổi (§6.3).
2. **Wireframe màn "Hộp thư hỗ trợ" admin (Q9):** điều kiện tiên quyết cho phase handoff (state `HUMAN` + chat admin + presence). Người trực đã rõ là admin (Q8) — chỉ còn thiếu wireframe.
3. **Thư viện PII cho production (Q6):** nếu audit chặt → cân nhắc Presidio thay regex tự viết. Nợ kỹ thuật; MVP dùng regex + test corpus, lệch bắt rộng.
4. **Tách store vault riêng (Q5):** phòng thủ theo lớp cho production nếu audit yêu cầu; MVP để chung Redis (đã mã hóa app-level).
5. **Router zero-retention (§6.3.1):** bật lọc provider không train/không lưu, lưu ảnh chụp setting + kiểm định kỳ — việc vận hành, không chặn code.
6. **Mã hóa app-level field `vault` (Q5):** ❌ chưa implement — `VAULT_ENC_KEY` mới dành chỗ trong `config.py`, session store đang lưu vault plaintext (in-memory/Redis).
7. **Form field phone/email ở CONTACT (Q6):** ❌ widget hiện chỉ có ô chat chung — thu PII đang dựa hoàn toàn vào lưới regex (`_capture_contact_from_vault`); form field tất định là việc phase sau.
8. **Khai báo `POST /chat/message` vào `openapi.yaml` (Q3):** ❌ chưa thêm.
9. **`VALIDATION_ERROR` đọc `details.fields`** để hỏi lại đúng field lỗi: hiện mới REPROMPT chung (§3.6).
10. ~~**Rút vault khi `CANCELLED`:** `maybe_drop_vault` mới xét `DONE/END/HUMAN`~~ — ✅ **đã fix 2026-07-24**: thêm `CANCELLED` vào danh sách terminal ở `session.py`, vault được rút sau khi hủy lịch (hết cửa sổ 2') thay vì giữ tới hết TTL 30'.

### 7.2 Rủi ro cần canh khi code

- **Rủi ro LLM (`§10`):** LLM bịa giá/dịch vụ → NLG **chỉ** diễn đạt param truyền vào, cấm tự sinh số liệu (mọi số từ API); NLU trích sai → validate JSON + dùng nút cho lựa chọn quan trọng; prompt injection → BE validate lại (guardrail 4).
- **Bất nhất nhỏ trong nguồn:** `chatbot-architecture.md §3.1` ghi tool SERVICE là `GET /services?date=`; path chuẩn (§4 + `openapi.yaml`) là `GET /shops/{shopId}/services?date=`. DD dùng path chuẩn — nên sửa nguồn cho khớp.
- **No-show** vẫn là ❓ (`business-analysis-draft.md`): chatbot không tạo trạng thái này; nếu retrieve booking `no_show` thì hiển thị read-only.

---

## 🔍 Auto Self-Review

**Đối chiếu với nguồn — điểm đã kiểm:**
- Mọi mã lỗi ở Mục 3.6/4/5 đều tồn tại trong catalog `api-design.md §0.2` và enum `Error` của `openapi.yaml` (L671–683): `SLOT_CONFLICT, PHONE_BLOCKED, THERAPIST_OFF_SHIFT, INVALID_COMBO, PARTY_SIZE_EXCEEDED, ADDON_WITHOUT_COURSE, THERAPIST_NOT_ALLOWED, MODIFY_DEADLINE_PASSED, SHOP_CHANGE_NOT_ALLOWED, EDIT_TOKEN_EXPIRED, VALIDATION_ERROR, INTERNAL_ERROR`. **Không** chế mã mới.
- Map A-flow ↔ mã lỗi khớp `usecase-userstories-processflow.md §2` (A3=combo, A4=off-shift, A5=NG, A6=conflict, A7=lỗi hệ thống, A8=>3 người) và cột BR của catalog. A1/A2 đã tách đúng là **200 rỗng**, không phải lỗi.
- Schema `BookingCreateRequest`/`BookingCreated`/`CustomerInfo` trích đúng field & ràng buộc từ `openapi.yaml` (L560–661): `party_size` 1–3 khớp `len(reservations)`, `therapist_*` loại trừ nhau/chỉ 1 người, `edit_token_expires_in=120`.
- BR viện dẫn (BR-01/02/04/05/06/07/08/09/10/14/15/16/17/18) khớp bảng `business-analysis-draft.md §3`.

**Điểm CHƯA chắc / rủi ro DD (đọc kỹ trước khi code):**
- **Q1–Q9 (Mục 7.0) đã chốt buổi rà soát 2026-07-23**, sau đó mentor **bỏ Q1/Q2** (banner đầu file) — phía BE không thay đổi gì. Vận hành (Q5–Q9) chốt Session/Vault, regex PII, người trực = admin, phân kỳ handoff; phần quyết rồi nhưng **code chưa làm** liệt kê ở 7.1 mục 6–10.
- **Còn treo ở 7.1 (không chặn MVP):** region LLM production (Q7 — chờ mentor/khách), wireframe màn hộp thư admin (Q9 — điều kiện phase handoff), và các khoảng cách implement 6–10.
- Schema `/chat/message` (2.1) + Session Store/Vault (2.4/2.5) **đã chốt** ở Q3/Q5 và **đã chạy trong code** — không còn là "cần duyệt".
- Danh sách state và thứ tự chuyển (§3.2) đã **đồng bộ theo code** (`states.py`) — khác nguồn `chatbot-architecture.md §3.1` ba điểm (bỏ DURATION, tách COURSE↔ADDON, THERAPIST trước SLOT); nên cập nhật nguồn cho khớp. Chưa đối chiếu từng state với thuật toán slot thực tế trong `booking_helpers` (module BE) vì chatbot không gọi trực tiếp — chỉ qua `GET /slots`; nếu tham số `GET /slots` đổi, bảng §2.2 phải cập nhật theo `openapi.yaml`.
- DD gộp **cả service** thành một file (không tách mỗi sub-module một DD) vì các phần ràng buộc chặt; khi vào code từng sub-module (state_machine, pii, nlu…) có thể cần DD con chi tiết hơn ở mức hàm — DD này là lát cắt kiến trúc, chưa xuống mức từng hàm của mọi sub-module.
