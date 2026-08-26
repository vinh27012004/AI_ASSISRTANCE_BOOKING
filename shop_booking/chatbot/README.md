# Chatbot service — AI đặt lịch (Giai đoạn 2)

Client hội thoại của `shop_api` (giống FE web). **State machine (code) điều khiển luồng; LLM
chỉ làm NLU①/NLG⑥** — không chứa business logic, BE là chốt chặn cuối (validate 2 tầng).

Thiết kế đầy đủ: [`../../UC-US-BA-APIDESIGN/detail-design/DD_chatbot.md`](../../UC-US-BA-APIDESIGN/detail-design/DD_chatbot.md)
· kiến trúc: [`../../UC-US-BA-APIDESIGN/chatbot-architecture.md`](../../UC-US-BA-APIDESIGN/chatbot-architecture.md).

## Chạy

```bash
# từ thư mục chatbot/ (dùng chung .venv ở gốc repo)
cp .env.example .env          # điền SHOP_API_CHANNEL_KEY (+ LLM_* nếu có router)
python -m flask --app wsgi run --port 5100
#   POST http://127.0.0.1:5100/chat/message   {conversation_id, text, lang}
#   GET  http://127.0.0.1:5100/health

python tests/test_chatbot.py  # test offline — KHÔNG cần LLM/Redis/shop_api
```

> Lưu ý Windows Application Control (như shop_api): gọi `python -m flask`, không gọi `flask.exe`.

## "Runnable offline" — mặc định không cần hạ tầng

| Thành phần | Chưa cấu hình | Cấu hình prod |
|---|---|---|
| LLM (NLU/NLG) | rule-based/câu mẫu (`LLM_BASE_URL` rỗng) | router OpenAI-compatible (`llm_client.py`) |
| Session store | in-memory (`REDIS_URL` rỗng) | Redis (`session.py`) |
| HTTP → shop_api | urllib (stdlib) | — |

Nhờ vậy lõi state machine + PII test được không cần mock LLM (bước ③④⑤ là code — mẹo test §9).

## Bản đồ module (DD §1.1)

| File | Vai trò |
|---|---|
| `orchestrator.py` | Vòng 6 bước `handle_turn` + `run_state_action` + map error.code (§3.1/§3.6) |
| `state_machine.py` · `states.py` | `next_state`, `merge_params`, token nút — code deterministic (§3.2/§3.3) |
| `nlu.py` · `llm_client.py` | Bước① trích param → JSON + validate; adapter router (§3.4, Q4) |
| `nlg.py` · `templates.py` | Bước⑤⑥ ghép template + sinh câu, đa ngôn ngữ vi/en/ja (§7) |
| `pii.py` | Mask/unmask/mask_response + Vault (SĐT/email/mã đặt chỗ — §6, Q6) |
| `session.py` | Session + store (TTL sliding 30', rút vault sau 2' — Q5) |
| `shop_api_client.py` | Gọi endpoint GĐ1 như client **public** (giống FE web) — không auth kênh riêng |
| `answers/` | Tủ tra cứu — bảng "loại câu hỏi → gọi API nào" (xem mục dưới) |
| `retrieval.py` · `answers/faq.py` · `data/faq.md` | RAG: hybrid BM25 + vector, trộn RRF (xem mục dưới) |
| `main.py` · `wsgi.py` | Flask `POST /chat/message` (§2.1, Q3) |

## Hai làn: điền đơn ↔ hỏi thông tin

`handle_turn` có một **chốt gác cửa** (`_is_question`) ngay sau NLU: lượt này là giá trị điền
vào slots, hay là câu HỎI? Câu hỏi rẽ sang `answers/` (tủ tra cứu) — tra API, trả lời, đọc lại
câu đang dở, **không đụng `session.state` lẫn `slots`**.

- Loại câu hỏi phủ: giờ mở cửa lúc X **hoặc theo ngày** (`/timeline`), địa chỉ/SĐT + danh sách
  cửa hàng (`/shops`), ngày nghỉ (`/availability`), giá gói (`/services`), cửa hàng cùng khu vực
  (khớp token địa chỉ), **lọc theo số nhân viên + giới tính** (`/timeline`).
- LLM hay gán `question_type=other` cho cả câu ta trả lời được -> `answers.resolve` suy lại bằng
  luật trước khi bó tay.
- Resolver **không cầm `Session`** (chỉ nhận `QueryCtx` chỉ-đọc). Muốn điền ô thì trả
  `Answer.suggest` để orchestrator đưa qua `sm.merge_params` — giữ MỘT cửa ghi duy nhất nên
  `_invalidate` (BR-04/BR-07) vẫn chạy.
- Câu trả lời chứa số liệu thật -> key `INFO` nằm trong `_LITERAL_SAFE_KEYS`, LLM không viết lại.
- Nghi ngờ thì ưu tiên luồng đặt lịch: KHÔNG tin một mình `question_type` của NLU (nó gán nhầm
  "other" cho `Sendai`, "course_price" cho `Gói đầu tiên`) — câu phải có DẤU HIỆU HỎI (`?` hoặc
  từ để hỏi: nào/đâu/bao nhiêu…) mới được rẽ sang làn hỏi đáp.
- Lạc đề 3 lượt liên tiếp -> đọc số cửa hàng (`_OFFTOPIC_LIMIT`).

Thêm loại câu hỏi mới = thêm một dòng vào `answers.RESOLVERS`.

## FAQ / RAG — lưới hứng cuối

Câu hỏi mà đáp án nằm trong VĂN BẢN (chính sách, quy trình) chứ không nằm trong bảng nào:
đổi/hủy lịch, tối đa mấy người, add-on đặt riêng được không, đến muộn thì sao. Trước đây mỗi
loại như vậy phải viết một resolver + thêm luật vào `_detect_question`; giờ **thêm một mục
`## ` vào [`data/faq.md`](data/faq.md) là xong**, không đụng code.

Vị trí trong luồng: khi không resolver nào nhận, `answers.resolve` giao cho `faq.answer`.
`_is_question` cũng đã được nới — câu rõ ràng là hỏi nhưng không gọi được tên loại thì gán
`question_type="faq"` thay vì rơi tuột về luồng đặt lịch.

**Retrieval** (`retrieval.py`) là hybrid, trộn bằng RRF:

| Nhánh | Gánh việc gì | Cần gì |
|---|---|---|
| BM25 | Thuật ngữ hiếm khớp nguyên văn (`momihogushi 30`, tên chi nhánh) | stdlib, offline |
| Vector | Câu diễn đạt khác ("hủy trước bao lâu" ↔ "chính sách thay đổi") | `EMBEDDING_*` |

Không cấu hình `EMBEDDING_*` → BM25-only, vẫn dùng tốt. Không dùng vector DB: corpus cỡ vài
trăm mục thì quét tuyến tính nhanh hơn mọi thứ khác. Trộn bằng RRF (`1/(60+hạng)`) chứ không
cộng điểm có trọng số — hai thang điểm khác nhau, chuẩn hóa là nguồn chỉnh tay bất tận.

Bốn ràng buộc **không được nới**:

1. **Chỉ chính sách/quy trình vào corpus.** Giờ mở cửa, giá, ngày nghỉ, địa chỉ vẫn phải đi
   qua `shop_api` — dữ liệu sống, ghi vào file là sai ngay hôm sau.
2. **Chunk trả cho khách nguyên văn, không qua LLM.** Đổi lại: không bịa, không thêm round
   trip, và chunk không bao giờ vào prompt nên không có đường prompt injection gián tiếp.
3. **Truy vấn là text ĐÃ MASK** (`ctx.raw_text`) — bật nhánh vector là câu hỏi bay sang nhà
   cung cấp thứ hai.
4. **Corpus review qua git.** Đừng làm bảng cho staff sửa trong admin: ai sửa được file là
   nói thay bot được.

Sửa `data/faq.md` xong thì chạy `python tests/check_faq.py` — nó in bảng câu hỏi mẫu → mục
nào được chọn, và có cả danh sách câu **phải bị từ chối** (bot trả lời tự tin nhưng lạc chủ
đề còn tệ hơn bot nói "em chưa hỗ trợ được"). Không nhận ra câu nào thì thêm dòng `> ` vào
mục tương ứng.

## Đọc log

`logs/chatbot.log` — mỗi lượt chat là MỘT khối, đọc từ trên xuống thấy đủ input → xử lý → output:

```
┌─ LƯỢT 3 · conv=demo · vào state=GREETING
│ IN     Cửa hàng Sendai
│ ①NLU   rule_based 0.00s · intent=book · qt=-      ← "(bỏ qua — …)" nếu nhánh không qua NLU
│ LANE   TASK                         ← điền đơn / QUERY (hỏi) / META (chào, sửa, hủy)
│ ②GỘP   shop_text: None→'Cửa hàng Sendai'
│ ③STATE GREETING → SHOP
│ ④API   GET /shops/2/availability → 200 7ms
│ ③STATE SHOP → DATE                  ← bước ④ khớp được tên nên đẩy tiếp
│ ⑥NLG   DATE · câu mẫu
│ OUT    Anh/chị muốn đặt vào ngày nào ạ? …
│ INTENT META:chào → ask_info:shops_list → book ◀   ← vệt intent cả hội thoại, ◀ = lượt này
└─ ra state=DATE · 0.01s · shop=2
```

**Intent**: mỗi lượt để lại một dấu, kể cả lượt KHÔNG qua NLU (câu chào, menu sửa/hủy — chúng đọc
bằng luật). Dòng `LANE` chỉ kèm cảnh báo `⚠ NLU trích intent=…` khi làn đi KHÁC intent NLU trả về
(vd NLU bảo `modify` nhưng chưa có booking nào nên vẫn phải điền đơn) — mọi lượt đều in thì thành
nhiễu, không ai đọc.

Khối phát ra một lần ở cuối lượt (`app/turnlog.py`) nên nhiều hội thoại song song không xen kẽ
nhau. Chi tiết thô — system prompt, raw response LLM, params/body shop_api — nằm ở mức **DEBUG**:
đặt `LOG_LEVEL=DEBUG` trong `.env` khi cần soi sâu.

## Quan hệ với BE

Chatbot dùng lại **nguyên bộ API GĐ1** như một client public (giống FE web) — **không** cần thay
đổi gì ở `shop_api`. Chống bấm đúp do BE tự lo bằng dedup thời gian 120s (cùng khách + shop +
ngày + giờ → trả lại booking cũ).

> Trước đây từng cân nhắc thêm API key kênh (`X-Api-Key`) + `Idempotency-Key`, nhưng đã **bỏ**
> theo yêu cầu mentor: chatbot chỉ gọi API bình thường.

## Luồng bước (đã tinh chỉnh)

`SHOP → DATE → PARTY_SIZE → COURSE → ADDON → THERAPIST(1 người) → SLOT → CONTACT → CONFIRM → CREATE → DONE`

- **Bỏ bước hỏi thời lượng**: mỗi course đã kèm sẵn `duration_min` (hiện luôn trên nút).
- **COURSE và ADDON là hai bước riêng**: chọn course chính trước, rồi chọn add-on hoặc "Không thêm".
  Cả nhóm dùng **chung** course và add-on (BR-10, BA cập nhật) — hỏi MỘT lần, không lặp theo từng
  người. Một câu nêu nhiều add-on ("Ashitsubo với Hot Stone") nhận hết, khớp bằng
  `matching.pick_all` (không phải `pick_unique` — 2 tên khớp không phải là "mơ hồ" ở đây).
  Danh sách gói/add-on đọc ra đều **đánh số**, nên khách trả lời bằng số cũng nhận
  (`matching.pick_by_index`).
- **Khớp tên hạ dần 3 tầng** (`matching.pick_unique`): chuỗi-con → theo TỪ (`"Shibuya đi"`) →
  khớp MỜ chịu lỗi gõ (`"Momihogishi 120p"` → `Momihogushi 120`). Mơ hồ ở tầng nào cũng dừng và
  hỏi lại, không đoán bừa.
- **Đổi cửa hàng giữa chừng**: bắt bằng Ý ĐỊNH (`nlu.is_change_shop_request`) vì nhánh rule-based
  không biết tên cửa hàng. `sm.clear_shop` dọn course/add-on/nhân viên/giờ (đều mang id riêng của
  shop) nhưng giữ ngày, số người, liên hệ.
- **Nhóm không đủ chỗ**: dò `_max_party_fit` để nói ĐÚNG nút thắt (số người, không phải ngày hay
  add-on) và chỉ mời "chuyển cửa hàng" khi `_shops_fitting_party` thực sự tìm được nơi khác.
- **THERAPIST trước SLOT**: chỉ định nhân viên trước → `GET /slots?therapist_id=` chỉ hiện giờ người đó
  thực sự rảnh (không dính "người này bận giờ đó"). Nhóm ≥2 bỏ qua THERAPIST → SLOT hiện mọi giờ. Nếu nhân
  viên chỉ định kín cả ngày → bot mời đổi người / để quán sắp / đổi ngày.
- **Sửa/hủy trong phiên** (UC-02/03): sau `DONE` có nút `[✏️ Sửa lịch]` / `[🗑 Hủy lịch]`. Sửa dùng
  `X-Edit-Token` (≤2 phút, BR-17) → `PATCH /bookings/{code}`; hủy dùng email → `cancel`. Quá cửa sổ 2'
  (vault đã rút — Q5) thì bot hướng dẫn dùng trang Quản lý đặt chỗ.

## Chưa làm (dời phase sau — DD 7.1)

- **Human handoff** đầy đủ (state `HUMAN` + màn hộp thư admin) — MVP chỉ nút `[📞 Gọi cửa hàng]` (Q9).
- Sửa/hủy **sau cửa sổ 2 phút** ngay trong chat (retrieve + email) — hiện chuyển sang trang Quản lý.
- Region LLM production (Q7), thư viện PII/Presidio, tách store vault (Q5) — nợ prod.
