# DD_chatbot — Detail Design: AI Chatbot đặt lịch (giai đoạn 2)

> Nguồn: `chatbot-architecture.md` (thiết kế mức cao) + `business-analysis-draft.md` (BR) + `api-design.md` (catalog lỗi §0.2/§0.2b, 7 quyết định) + `openapi.yaml` (schema) + `usecase-userstories-processflow.md` (UC/US/A1–A8).
> Mọi khẳng định nghiệp vụ dưới đây trích số hiệu; luật đổi thì chỉ cập nhật số hiệu.
> **Quy ước quan trọng:** chatbot là **client** của `shop_api` (giống FE), **không** chứa business logic. BE vẫn là chốt chặn cuối (validate 2 tầng — `api-design.md` quyết định #3). DD này mô tả *client* đó, không định nghĩa lại luật của BE.

---

## 1. Tổng quan & phạm vi

### 1.1 Module này là gì

Đây **không** phải một module trong repo giai đoạn 1 (`shop_api`/`shop_web`). Theo skill ("spec sinh module mới → thêm dòng và giải thích"), chatbot là **một service mới, repo riêng** (`chatbot-architecture.md §8`). DD gom cả service này thành một lát cắt dọc vì các thành phần ràng buộc chặt với nhau (state machine ↔ error code ↔ tool API ↔ PII mask).

Service gồm các **sub-module** (mỗi cái ánh xạ 1 đơn vị code triển khai được — theo lộ trình MVP `chatbot-architecture.md §9`):

| Sub-module | File/nhóm dự kiến | Vai trò | Nguồn |
|---|---|---|---|
| `chat_widget` | FE React nhúng trong `shop_web` | Ô chat + render nút lựa chọn | §2, §7 |
| `orchestrator` | `orchestrator.py` | Chạy vòng xử lý 6 bước cho mỗi lượt chat | §1, §2 |
| `state_machine` | `state_machine.py` + `states.py` | **Code deterministic**: định nghĩa state, chọn state kế, gọi tool, validate sơ bộ | §3 |
| `nlu` | `nlu.py` | Gọi LLM bước ①: trích param → JSON cố định (§3.4) | §3.4 |
| `nlg` | `nlg.py` + `templates/` | Bước ⑤⑥: ghép template + gọi LLM sinh câu | §1, §3.1 |
| `llm_client` | `llm_client.py` | Adapter router OpenAI-compatible; đổi provider = đổi `base_url`+`api_key` | §6.3 |
| `pii_masker` | `pii_masker.py` + Vault | Che SĐT/email/mã đặt chỗ trước khi ra LLM (§6) | §6 |
| `session_store` | Redis client | Mỗi `conversation_id`: state, slots, vault, lịch sử | §2 |
| `api_client` | `shop_api_client.py` | Gọi các endpoint giai đoạn 1 (§4). **Chỉ code gọi, LLM không gọi** | §2, §4 |
| *(BE thay đổi ①)* | middleware trong `shop_api` | API key kênh (`X-Api-Key`) + rate limit — Mục 7.0-Q2 | §8 |
| *(BE thay đổi ②)* | handler `POST /bookings` | Đọc `Idempotency-Key` + bảng `key→booking` — Mục 7.0-Q1 | api-design QĐ#2 |

### 1.2 UC/US phủ

- **UC-01** Đặt lịch (12 bước) — lõi. Chatbot dẫn khách đi đúng 12 bước bằng state machine (`chatbot-architecture.md §3.1` bám UC-01).
- **UC-04** Tra slot · **UC-05** Nhận dạng khách · **UC-06** Kiểm tra NG list · **UC-07** Chỉ định therapist — các include/extend của UC-01.
- **UC-02** Sửa · **UC-03** Hủy — "sửa/hủy trong phiên" (MVP §9 mục 8).
- **US-09** (AI chatbot là client dùng chung bộ API) — chính là US mà giai đoạn 1 đã thiết kế API hướng tới; hệ quả: "logic đặt 100% ở BE, FE/chatbot chỉ là client".

### 1.3 Cơ chế auth (client → `shop_api`) — `api-design.md §0.3`

Chatbot dùng **đúng 3 cơ chế như FE**, cộng một lớp kênh mới:

| Thao tác | Auth với `shop_api` |
|---|---|
| Luồng đặt chỗ (shops/services/slots/therapists/lookup/POST bookings) | **Public** |
| Sửa nhanh ≤2 phút sau khi tạo trong phiên | header `X-Edit-Token` (JWT TTL 2 phút — BR-17) |
| Sửa/hủy sau đó | `booking_code` + `email` trong body (BR-15) |
| **Toàn bộ request từ kênh chatbot** | **+ API key kênh**: header `X-Api-Key`, BE đối chiếu bảng `channel_api_key` (đã chốt — Mục 7.0-Q2) |

### 1.4 Endpoint/hàm public module chịu trách nhiệm

- **Đối ngoại (widget → service):** `POST /chat/message` — endpoint hội thoại (MỚI; schema đã chốt ở Mục 2.1 — Mục 7.0-Q3; cần thêm vào `openapi.yaml`).
- **Hàm lõi (nội bộ):** `handle_turn(conversation_id, user_text) -> BotReply` (orchestrator), `next_state(session) -> State` (state machine), `extract(text, lang) -> NluResult` (nlu), `render(state, session, lang) -> str` (nlg), `mask(text) / unmask(param) / mask_response(obj)` (pii).

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
  "reply_text": "string",           // câu bot đã sinh (NLG), đã unmask? -> KHÔNG, xem ghi chú
  "state": "SERVICE",               // state hiện tại (debug/telemetry)
  "ui": { "buttons": [{"label":"もみほぐし 60'","value":"course:3"}] },  // §7 lựa chọn dạng nút
  "done": false                     // true khi vào DONE/END/HUMAN
}
```
- `reply_text` là văn bản hiển thị cho **khách** → chứa PII thật của chính khách (vd đọc lại SĐT ở state CONFIRM) là hợp lệ vì đây là kênh của mình, **không** phải LLM. Việc unmask xảy ra khi ghép câu cuối (sau bước ⑥), trước khi trả widget.
- `conversation_id` = khóa Session Store, cũng là `Idempotency-Key` khi `POST /bookings` (§4 — nhưng xem cảnh báo Mục 7 về việc BE chưa đọc header này).

### 2.2 Tool `shop_api` do State Machine gọi (bước ④) — không phải LLM

Tất cả trích thẳng `openapi.yaml`/`api-design.md`; **không** định nghĩa lại kiểu.

| State/thao tác | Method + path | Request (schema) | Response (schema) | Ghi chú |
|---|---|---|---|---|
| `SHOP` | `GET /shops` | — | `[{id, shop_code, name, address, phone}]` | render nút chọn shop (§4) |
| `SERVICE` | `GET /shops/{shopId}/services?date=&party_size=` | query | `{courses[], addons[{…, restricted_course_ids[]}], reason}` | `restricted_course_ids` để disable add-on cấm sớm (BR-09); `reason:"SHOP_CLOSED"` = A1 |
| `SLOT` | `GET /shops/{shopId}/slots?date=&party_size=&course_id=&addon_ids=&therapist_id=&therapist_gender=` | query (openapi L65–90) | `{slots: ["14:00", …]}` | rỗng = A2. **Chỉ hiển thị**, BE re-check lúc tạo (BR-08) |
| `THERAPIST` | `GET /shops/{shopId}/therapists?date=` | query | `[{id, name, gender}]` | **chỉ khi `party_size==1`** (BR-04). Chỉ lộ tên+giới tính |
| `CONTACT` | `POST /customers/lookup` | `{phone}` | `CustomerInfo{member_type, rank, visit_count}` | chặn NG tại chỗ (BR-06) → 403 `PHONE_BLOCKED` |
| `CREATE` | `POST /bookings` | `BookingCreateRequest` (openapi L581) | `BookingCreated{…, edit_token, edit_token_expires_in}` | header `Idempotency-Key=conversation_id` — BE sẽ đọc key (đã chốt — Mục 7.0-Q1) |
| sửa trong phiên | `PATCH /bookings/{bookingCode}` | `BookingUpdateRequest` + header `X-Edit-Token` | `Booking` | edit_token còn hạn 2 phút (BR-17) |
| hủy trong phiên | `POST /bookings/{bookingCode}/cancel` | `{email}` | `Booking{status:"cancelled"}` | idempotent (api-design 2.3) |
| tra lại | `POST /bookings/retrieve` | `{booking_code, email}` | `Booking{…, can_modify}` | dùng khi edit_token hết hạn |

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
- Code **validate JSON này trước khi merge**; sai schema → coi như không trích được → hỏi lại (§3.4, ranh giới "LLM hiểu" ↔ "code quyết"). Đây là chỗ chống prompt injection tầng client.
- `course`/`therapist` là **text/placeholder tự do** → phải map về `id` qua tool response, không tin nguyên văn (Mục 10 "NLU trích sai param").

### 2.4 Session Store (Redis) — schema MỚI (do DD định nghĩa, không có trong ERD)

Khóa `conversation_id` (chính sách Q5: TTL **sliding 30'** refresh mỗi lượt, **rút vault** sau cửa sổ sửa nhanh 2', **mã hóa app-level** riêng field `vault`, **một Redis** cho MVP):
```
{
  "state": "SERVICE",
  "lang": "vi",
  "slots": { "shop_id":1, "date":"2026-07-23", "party_size":2, "duration":60,
             "course_id":3, "addons":[7], "slot":"14:00",
             "therapist_id":null, "therapist_gender":null,
             "wanted_time":"08:00" },       // "giờ mong muốn" ưu tiên gợi ý (§3.3)
  "vault": { "{{phone_1}}":"…", "{{email_1}}":"…", "{{code_1}}":"…" },  // §6 + Q5 — mã hóa app-level riêng field này, rút sau 2'
  "booking_code": null, "edit_token": null, "edit_token_expires_at": null,
  "history": [ {role, masked_text} ]        // đã mask, TTL 30–90 ngày (§6.3)
}
```

### 2.5 PII Vault — `chatbot-architecture.md §6.2`

| Loại | Cách bắt | Placeholder |
|---|---|---|
| SĐT | regex (VN + JP) | `{{phone_N}}` |
| Email | regex | `{{email_N}}` |
| Mã đặt chỗ | regex `\d{8}-\w+-\w+` (khớp format `{yyyyMMdd}-{shop_code}-{rand}` — api-design 1.5) | `{{code_N}}` |
| Tên khách | **từ response API — không đưa vào context LLM** | — |

### 2.6 Bảng/model đụng tới

- **Không** ghi thẳng DB của `shop_api`. Mọi đọc/ghi qua API → các bảng phía sau thuộc `shop_api` (`shop, course, addon, therapist, shift, customer, ng_list, booking, reservation, reservation_addon, combo_restriction` — `business-analysis-draft.md §1`). Chatbot **read qua API**, **write** duy nhất qua `POST/PATCH/cancel bookings`.
- **Persistence mới:** Session Store (Redis, §2.4) + PII Vault (§2.5) thuộc service chatbot; **bảng `channel_api_key`** (auth kênh — Q2) và **bảng `idempotency_key→booking`** (Q1) thuộc `shop_api` — cả bốn **chưa có trong `erd-schema.sql`**, cần bổ sung migration (Mục 7.0 + 7.1).

### 2.7 Mã lỗi nhận từ `shop_api` (client xử lý) — catalog `api-design.md §0.2`

Chatbot **không sinh** mã lỗi nghiệp vụ; nó **nhận** từ BE và chọn nhánh + template. Bảng đầy đủ ở Mục 5. Ngoài ra kênh chatbot có thể gặp mã hạ tầng: **429** (rate limit nhóm 2 và login — api-design §2/§3.0, và rate limit kênh chatbot §8) → template "thử lại sau".

---

## 3. Pseudocode / thuật toán

### 3.1 Vòng xử lý 1 lượt (orchestrator) — `chatbot-architecture.md §1`

```
handle_turn(cid, user_text):
    session = session_store.load(cid)              # tạo mới nếu chưa có → state=GREETING
    # ① NLU (LLM) — chỉ trích param
    masked = pii_masker.mask(user_text, session.vault)   # § trước khi ra LLM (bước ⑥.1)
    nlu = nlu.extract(masked, session.lang)              # JSON §3.4
    if not valid_schema(nlu): return reprompt(session)   # sai schema → hỏi lại
    session.lang = detect_or_keep(nlu, session.lang)     # §7 đa ngôn ngữ

    # xử lý intent điều hướng (handoff/cancel/modify) trước khi merge slot
    if nlu.intent == "handoff": return go_handoff(session)   # §7; MVP: chỉ nút [📞 Gọi cửa hàng] (Q9), state HUMAN dời phase sau

    # ② MERGE — gộp entity đã trích vào slots (chỉ field không null)
    merge_params(session.slots, nlu.entities)            # §3.3 (gồm xóa slot mâu thuẫn)

    # ③ STATE MACHINE (code) — chọn state kế
    session.state = next_state(session)                  # §3.2

    # ④ VALIDATE + CALL API (code) — nếu state cần dữ liệu
    api_result = run_state_action(session)               # gọi tool §2.2; xử lý error §3.6

    # ⑤ BUILD PROMPT (code) — template[state] + params + options + lang
    prompt = nlg.build_prompt(session.state, session.slots, api_result, session.lang)

    # ⑥ NLG (LLM) — sinh câu tự nhiên
    reply = nlg.generate(prompt)
    reply = pii_masker.unmask(reply, session.vault)      # trả PII thật của chính khách cho widget
    session_store.save(cid, session)
    return BotReply(reply, ui=buttons_for(session.state, api_result), done=is_terminal(session.state))
```
- **LLM chỉ ở ① và ⑥.** Bước ②③④⑤ là code thuần → unit-test không cần LLM (§9 mẹo test, Mục 6).

### 3.2 Hàm chọn state kế (bước ③) — `chatbot-architecture.md §3.1–3.2`

Thứ tự state (điều kiện vào + slot phải có để rời):
`GREETING → SHOP(shop_id) → DATE(date) → PARTY_SIZE(1–3) → DURATION(duration) → SERVICE(course_id) → SLOT(slot) → THERAPIST(therapist|skip, chỉ khi party_size==1) → CONTACT(phone,email) → CONFIRM(đồng ý) → CREATE → DONE`

```
next_state(session):
    for st in ORDER:                       # đúng thứ tự bảng §3.1
        if not entry_condition(st, session): continue     # vd THERAPIST khi party_size>1 → bỏ qua (BR-04)
        if not slots_satisfied(st, session): return st     # state đầu tiên còn thiếu slot
    return DONE
```
- Khách nói gộp *"mai 2 người 60 phút"* → merge lấp `date,party_size,duration` một lượt → nhảy thẳng `SERVICE`, không hỏi lại 3 câu (§3.2).

### 3.3 Merge param + vô hiệu hóa slot mâu thuẫn (bước ②) — `chatbot-architecture.md §3.2`

```
merge_params(slots, ent):
    for k,v in ent: if v is not None: slots[k] = v
    if changed(party_size) and slots.party_size > 1:
        slots.therapist_id = None; slots.therapist_gender = None    # BR-04: nhóm không chỉ định
    if changed(course_id or party_size or duration or date):
        slots.slot = None            # slot cũ không còn chắc hợp lệ → buộc qua lại SLOT (BR-07)
    keep slots.wanted_time           # giữ "giờ mong muốn" để ưu tiên gợi ý quanh giờ đó (§3.3)
```
- *"à cho 3 người"* giữa chừng → `party_size=3`, therapist bị **xóa** (BR-04), state quay về nhánh phù hợp.

### 3.4 PII mask / unmask (bước bao quanh ①⑥) — `chatbot-architecture.md §6.1`

```
mask(text, vault):   regex bắt phone/email/code → tạo {{phone_N}}… lưu vault → thay vào text
unmask(reply, vault): thay placeholder trong câu bot bằng giá trị thật (chỉ cho widget của khách)
before_call_api: state machine thay placeholder → giá trị thật khi build BookingCreateRequest
after_api:  mask_response(obj) trước khi bất kỳ field nào lọt vào context LLM (tên khách bỏ hẳn)
```
- **Thu PII tất định (Q6):** state CONTACT lấy phone/email qua **form field widget**, đẩy thẳng vào vault; regex `mask()` chỉ là **lưới hứng** cho PII khách lỡ gõ giữa câu → regex **lệch bắt rộng** (mask thừa hơn sót).
- **Mã đặt chỗ (Q6):** mask bằng **cả** regex `\d{8}-\w+-\w+` **lẫn so khớp giá trị thật** trong vault khi session đã có `booking_code`.
- **Tên khách (Q6):** quy tắc cứng — `mask_response()` strip field tên trước khi bất kỳ thứ gì vào prompt, không dựa regex.
- Kết quả: nhà cung cấp LLM chỉ thấy *ý định*, không nhận PII (§6, quyết định 2 & 4).

### 3.5 State CREATE — `POST /bookings` (bước ④ tại CONFIRM=yes)

```
on CREATE:
    assert session.state was CONFIRM and confirm==yes          # guardrail 1 (§4.2): không ghi khi chưa xác nhận
    body = build_booking_request(session.slots, unmask(phone,email))   # đúng BookingCreateRequest
    resp = api.post_bookings(body, headers={Idempotency-Key: cid})     # BE đọc key (đã chốt Q1): gửi lại cùng cid -> trả đúng booking cũ
    if 201:
        session.booking_code, session.edit_token = resp.booking_code, resp.edit_token
        session.edit_token_expires_at = now + resp.edit_token_expires_in   # =120s (BR-17)
        state = DONE  → template "thành công + mã đã gửi email" (BR-15), mời sửa nhanh (BR-17)
    else: handle_api_error(resp.error.code)                    # §3.6
```
- **Không** tự sinh `booking_code` — BE sinh (BR-12). **Không** rollback vì email; email do BE gửi sau commit qua SES (api-design quyết định #4).

### 3.6 Map error.code → nhánh state (bước ⑤⑥ khi API lỗi) — `chatbot-architecture.md §4.1`

```
handle_api_error(code):
    SLOT_CONFLICT        -> state=SLOT;  đọc details.suggested_slots; template A6
    PHONE_BLOCKED        -> state=END;   template A5 (kèm details.reason, shop_phone)
    THERAPIST_OFF_SHIFT  -> state=THERAPIST/SLOT; template A4
    INVALID_COMBO        -> state=SERVICE; template A3
    PARTY_SIZE_EXCEEDED  -> nhánh handoff; template A8/BR-14 (shop_phone)
    ADDON_WITHOUT_COURSE -> state=SERVICE; "cần chọn course chính" (BR-01, xem Mục 5)
    THERAPIST_NOT_ALLOWED-> xóa therapist, state=CONTACT; (BR-04)
    MODIFY_DEADLINE_PASSED-> thông báo; (BR-16)
    VALIDATION_ERROR     -> hỏi lại field lỗi (details.fields)
    INTERNAL_ERROR / 429 -> giữ state, mời thử lại; (A7/BR-12)
```

---

## 4. Business rules enforcement

> **Chatbot KHÔNG enforce BR — chatbot chỉ hỏi/hiển thị/lọc sớm cho UX. Chốt chặn thật là `shop_api`** (validate 2 tầng — `api-design.md` quyết định #3; `chatbot-architecture.md §4.2` guardrail 4). Cột "vai trò client" nói rõ chatbot làm gì; cột "BE enforce ở" là nơi luật thực sự chạy.

| BR | Vai trò client (chatbot) | BE enforce ở | Mã lỗi nếu vi phạm |
|---|---|---|---|
| BR-14 (≤3 người) | state PARTY_SIZE giới hạn 1–3; >3 → nhánh handoff (A8) | `POST /bookings` bước 2 | 400 `PARTY_SIZE_EXCEEDED` |
| BR-04 (nhóm không chỉ định) | bỏ qua state THERAPIST khi `party_size>1`; xóa therapist khi đổi lên nhóm (§3.3) | `POST /bookings` bước 3 | 400 `THERAPIST_NOT_ALLOWED` |
| BR-01 (add-on phải kèm course) | state SERVICE luôn hỏi course trước add-on | `POST /bookings` (payload chỉ add-on) | 400 `ADDON_WITHOUT_COURSE` |
| BR-09 (combo cấm) | dùng `restricted_course_ids` để disable add-on sớm (A3) | `POST /bookings` bước 5 | 422 `INVALID_COMBO` |
| BR-06 (NG list) | `POST /customers/lookup` chặn ngay ở state CONTACT (A5) | lookup + `POST /bookings` bước 6 | 403 `PHONE_BLOCKED` |
| BR-05 (therapist có ca) | chỉ liệt kê người có ca (`GET /therapists?date=`) | `POST /bookings` bước 7 | 422 `THERAPIST_OFF_SHIFT` |
| BR-07 (slot phụ thuộc điều kiện) | gọi `GET /slots` với đủ tham số; đổi điều kiện → xóa slot cũ (§3.3) | thuật toán slot + re-check | — / 409 |
| BR-08 (slot hết realtime) | không tin `GET /slots`; xử lý 409 → gợi ý `suggested_slots` (A6) | `POST /bookings` transaction | 409 `SLOT_CONFLICT` |
| BR-10 (nhóm cùng course) | state SERVICE chọn **một** course cho cả nhóm; add-on riêng từng reservation | `POST /bookings` (course_id + reservations[]) | 400/422 |
| BR-02 (bội số 15') | chỉ chào các gói hợp lệ do API trả (không tự bịa — Mục 10) | admin/service data | 400 `VALIDATION_ERROR` |
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
| **A2** | Ngày hết slot (bước 6) | `GET /slots` → 200 `{slots:[]}` | quay state DATE (hoặc SLOT với ngày khác), "ngày này đã kín chỗ" |
| **A3** | Combo course+add-on cấm (bước 5) | 422 `INVALID_COMBO` `{course_id,addon_id}` | quay state SERVICE, đọc `message` BE, gợi ý bỏ/đổi add-on |
| **A4** | Therapist không có ca (bước 8) | 422 `THERAPIST_OFF_SHIFT` `{therapist_id}` | quay THERAPIST/SLOT: "đổi giờ hay bỏ chỉ định?" |
| **A5** | SĐT trong NG list (bước 9) | 403 `PHONE_BLOCKED` `{reason, shop_phone}` | state END: đọc `message`+`reason`, đưa `shop_phone`; **không** tạo booking |
| **A6** | Slot vừa bị chiếm (bước 11) | 409 `SLOT_CONFLICT` `{suggested_slots:[3 giờ]}` | quay SLOT, **hiện luôn** `suggested_slots` (không gọi lại `GET /slots`) |
| **A7** | Lỗi hệ thống (bước 11) | 500 `INTERNAL_ERROR` | giữ state CONFIRM, mời thử lại |
| **A8** | Nhóm >3 (bước 3) | 400 `PARTY_SIZE_EXCEEDED` `{shop_phone}` | nhánh handoff, "tối đa 3 người/lượt", đưa `shop_phone` |

Nhánh sửa/hủy (UC-02/03) ngoài A1–A8:
- Edit token hết 2 phút → 401 `EDIT_TOKEN_EXPIRED` → chuyển hướng: hỏi `booking_code`+`email` để `POST /bookings/retrieve` rồi `PATCH` (BR-17).
- Còn <1h → 422 `MODIFY_DEADLINE_PASSED` → đưa `shop_phone` (BR-16).
- Đòi đổi shop → 422 `SHOP_CHANGE_NOT_ALLOWED` (BR-18).

**Guardrails** (`chatbot-architecture.md §4.2`): (1) `POST /bookings` chỉ chạy khi state=CONFIRM & khách đồng ý; (2) LLM không có quyền gọi API — dù NLU trả param lạ, code vẫn đi theo bảng chuyển state; (3) bot không dùng PII ngoài đơn hiện tại (quyết định 4); (4) BE validate lại toàn bộ → chống prompt injection.

---

## 6. Test scenarios (acceptance)

> Repo **chưa có test runner** (không pytest/jest — CLAUDE.md). Đây là checklist verify thủ công + hạt giống cho unit test giai đoạn sau. **Mẹo (`chatbot-architecture.md §9`):** bước ③④⑤ là code → unit-test state machine bằng param giả, **mock LLM ở ①⑥**, assert state kế + tool được gọi.

**State machine (không cần LLM):**
- **T1 (nói gộp, §3.2):** *Given* slots rỗng · *When* NLU trả `{date, party_size:2, duration:60}` · *Then* `next_state==SERVICE`, đã gọi `GET /services`, **không** hỏi lại 3 câu.
- **T2 (BR-04, §3.3):** *Given* `party_size:1, therapist_id:5` · *When* merge `party_size:3` · *Then* `therapist_id==None`, state không vào THERAPIST.
- **T3 (BR-14/A8):** *Given* NLU `party_size:5` · *When* chọn state · *Then* nhánh handoff; nếu vẫn gửi BE → 400 `PARTY_SIZE_EXCEEDED`.

**Luồng đầy đủ (mock LLM):** — bám AC của US
- **T4 (US-01 AC2):** đặt 1 người thành công → nhận `booking_code` + câu "đã gửi email".
- **T5 (US-01 AC3 / A6 / BR-08):** `POST /bookings` → 409 · *Then* bot đọc `suggested_slots`, quay SLOT, không gọi thêm API.
- **T6 (US-02 AC2 / A4):** chỉ định therapist nghỉ ca → 422 `THERAPIST_OFF_SHIFT` · *Then* bot hỏi "đổi giờ hay bỏ chỉ định".
- **T7 (US-03 AC1 / BR-10):** nhóm 3 người, 1 course, add-on riêng → tạo booking 3 reservation cùng giờ.
- **T8 (A5 / BR-06):** SĐT NG → `POST /customers/lookup` 403 `PHONE_BLOCKED` · *Then* state END, đưa `shop_phone`, **không** POST bookings.
- **T9 (A3 / BR-09):** add-on cấm → 422 `INVALID_COMBO` · *Then* quay SERVICE.
- **T10 (A1):** ngày nghỉ → `GET /services` `reason:SHOP_CLOSED` · *Then* quay DATE.
- **T11 (BR-17):** sửa trong 2' bằng `X-Edit-Token`; sau 2' → 401 `EDIT_TOKEN_EXPIRED` → chuyển sang retrieve+PATCH.

**PII (§6 — unit test regex, MVP mục 2):**
- **T12:** input "SĐT 0901234567, email a@b.com" · *Then* text ra LLM chỉ có `{{phone_1}}`/`{{email_1}}`; body `POST /bookings` chứa giá trị thật; context LLM sau lookup **không** chứa tên khách.
- **T13:** mã đặt chỗ `20260720-S001-A1B2` trong tin nhắn → match regex `\d{8}-\w+-\w+` → `{{code_1}}`.

**Đa ngôn ngữ / handoff (§7):**
- **T14:** tin nhắn tiếng Nhật → `lang=ja` lưu session → mọi NLG truyền `ja`; gọi API vẫn bằng `id`.
- **T15:** intent handoff → MVP hiện nút `[📞 Gọi cửa hàng]` (Q9). *(Phase sau: state HUMAN, bot ngừng tự trả lời, nút `[💬 Chat nhân viên]` hiện khi có admin online — presence, Q8.)*

---

## 7. Open questions & rủi ro

### 7.0 Quyết định đã chốt (buổi rà soát 2026-07-23)

| # | Vấn đề | Quyết định | Kéo theo (việc phải làm) |
|---|---|---|---|
| **Q1** | Chống tạo booking trùng — BE hiện chỉ dedup theo thời gian 120s, chưa đọc header (`api-design.md` QĐ#2) | **BE bổ sung đọc `Idempotency-Key`** + bảng `key→booking`; chatbot dùng `conversation_id` làm key | Xử lý được cả case retry lẫn case **đổi nội dung cùng giờ trong 120s** (dedup thời gian không phân biệt được). Migration bảng key + sửa handler `POST /bookings`. **Đây là thay đổi BE thứ 2** |
| **Q2** | Auth kênh chatbot → `shop_api` (§8) | **API key tĩnh (hash)**: header `X-Api-Key`, bảng `channel_api_key(key_hash, name, rate_limit, active)`, rate-limit theo key | 401 khi thiếu/sai key, 429 khi vượt hạn — **độc lập** rate-limit nhóm 2 (10/60s) và login (5/60s). Cần spec + migration BE |
| **Q3** | Interface `/chat/message` (§2.1) | **Request/response đơn giản**, không streaming: `{conversation_id, text, lang} → {conversation_id, reply_text, state, ui.buttons[], done}` | Đủ MVP, gắn nút dễ. Nâng lên SSE sau **không phá** schema (chỉ đổi content-type). Cần thêm vào `openapi.yaml` |
| **Q4** | Cách trích entity NLU (§11.4) | **Tự viết prompt + validate JSON** (§3.4); **không** dùng function-calling riêng của model, **không** framework agent | Chạy đồng nhất trên mọi model qua router (hợp mục tiêu thử nhiều model cho tiếng Nhật — §10). Lưới "sai schema → hỏi lại" (§3.4) bắt lỗi format. Chuyển sang structured output sau này rất nhẹ (ranh giới "LLM trả JSON → code validate" không đổi) |
| **Q5** | Session Store & PII Vault (§2.4/2.5) | TTL **sliding 30'** (refresh mỗi lượt) + **rút vault** ngay sau cửa sổ sửa nhanh 2' (BR-17); **mã hóa app-level** riêng field `vault` (key env/KMS); **một Redis** cho MVP | Sau `DONE`+hết 2': xóa PII, giữ state/booking_code tới hết TTL. Tách store vault riêng để dành production nếu audit yêu cầu (7.1) |
| **Q6** | Độ phủ regex PII (§6.2) | Nguyên tắc **mask thừa hơn sót**; phủ SĐT VN+JP rộng (mã vùng, separator, `0120`); **thu phone/email qua form field ở CONTACT** (tất định — regex chỉ là lưới hứng); mã đặt chỗ mask bằng **cả** regex `\d{8}-\w+-\w+` **lẫn giá trị thật** trong vault; **tên khách không bao giờ vào context** (strip ở `mask_response`) | MVP: regex tự viết + unit-test corpus (§9 mục 2). Thư viện PII chuyên (Presidio…) = nợ cho production nếu cần audit (7.1) |
| **Q7** | Region LLM production (§6.4) | MVP = **router + masking**; production **hướng Bedrock/Azure Tokyo** (data JP, có DPA, hợp APPI) | Đổi provider chỉ sửa `base_url`+`api_key` (adapter §6.3). **Quyết định cuối chờ mentor/khách** — treo ở 7.1 |
| **Q8** | Ai trực chat nhân viên (§11.b) | **Tài khoản admin** trực (JWT `role=admin` sẵn có); màn "Hộp thư hỗ trợ" nằm trong khu admin `app/admin/*`; nút `[💬]` hiện theo **presence** (có admin online) | Không cần auth mới, không cần định nghĩa "giờ trực" cố định — có admin online thì hiện nút |
| **Q9** | Phân kỳ handoff (§11.c, §7 nguồn, MVP §9.9) | **MVP chỉ nút `[📞 Gọi cửa hàng]`** (đưa `shop_phone`); state `HUMAN` + chat admin + màn hộp thư **dời phase sau** | Điều kiện tiên quyết phase sau = **wireframe màn hộp thư admin** (chưa có) — không còn chờ quyết định nhân sự (đã có Q8). Treo ở 7.1 |

> **Hệ quả tổng:** phía BE có **2 thay đổi**, không phải 1 như `chatbot-architecture.md §0/§8` viết — middleware API key (Q2) **và** đọc `Idempotency-Key` (Q1). Cả hai (+ bảng của chúng) **đã được đặc tả**: `api-design.md §7`, `openapi.yaml` (scheme `channelApiKey`, header `Idempotency-Key` + response 200 replay, mã `RATE_LIMITED`/`CHANNEL_UNAUTHORIZED`), `erd-schema.sql` (bảng `idempotency_key`, `channel_api_key`). Việc còn lại: **implement** ở `shop_api` (model + route + middleware + migration alembic).

### 7.1 Dời phase sau / chờ mentor (không chặn MVP)

1. **Region LLM production (Q7):** chốt cuối dùng router hay Bedrock/Azure Tokyo — **chờ mentor/khách duyệt**. Hướng đã có, adapter `llm_client.py` sẵn để đổi (§6.3).
2. **Wireframe màn "Hộp thư hỗ trợ" admin (Q9):** điều kiện tiên quyết cho phase handoff (state `HUMAN` + chat admin + presence). Người trực đã rõ là admin (Q8) — chỉ còn thiếu wireframe.
3. **Thư viện PII cho production (Q6):** nếu audit chặt → cân nhắc Presidio thay regex tự viết. Nợ kỹ thuật; MVP dùng regex + test corpus, lệch bắt rộng.
4. **Tách store vault riêng (Q5):** phòng thủ theo lớp cho production nếu audit yêu cầu; MVP để chung Redis (đã mã hóa app-level).
5. **Router zero-retention (§6.3.1):** bật lọc provider không train/không lưu, lưu ảnh chụp setting + kiểm định kỳ — việc vận hành, không chặn code.

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
- **Q1–Q9 (Mục 7.0) đã chốt buổi rà soát 2026-07-23** — kỹ thuật (Q1–Q4) kéo theo **2 thay đổi BE** (API key kênh + đọc `Idempotency-Key`) và 2 bảng mới `shop_api`, **đã đưa vào spec BE** (`api-design.md §7`, `openapi.yaml`, `erd-schema.sql`); còn lại là implement + migration alembic. Vận hành (Q5–Q9) chốt Session/Vault, regex PII, người trực = admin, phân kỳ handoff.
- **Chỉ còn treo ở 7.1 (không chặn MVP):** region LLM production (Q7 — chờ mentor/khách) và wireframe màn hộp thư admin (Q9 — điều kiện phase handoff). Các mục 7.1 là dời-phase/chờ-mentor, đã tách khỏi phần đã chốt.
- Schema `/chat/message` (2.1) + Session Store/Vault (2.4/2.5) do DD đề xuất nhưng **đã chốt** ở Q3/Q5 — không còn là "cần duyệt".
- Danh sách state (§3.1 nguồn) và thứ tự chuyển (§3.2) chép từ `chatbot-architecture.md`; chưa đối chiếu từng state với thuật toán slot thực tế trong `booking_helpers` (module BE) vì chatbot không gọi trực tiếp — chỉ qua `GET /slots`. Nếu tham số `GET /slots` đổi, bảng §2.2 phải cập nhật theo `openapi.yaml`.
- DD gộp **cả service** thành một file (không tách mỗi sub-module một DD) vì các phần ràng buộc chặt; khi vào code từng sub-module (state_machine, pii_masker, nlu…) có thể cần DD con chi tiết hơn ở mức hàm — DD này là lát cắt kiến trúc, chưa xuống mức từng hàm của mọi sub-module.
