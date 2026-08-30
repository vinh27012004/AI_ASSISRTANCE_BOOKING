# Chatbot service — AI đặt lịch (Giai đoạn 2)

Client hội thoại của `shop_api` (giống FE web). **State machine (code) điều khiển luồng; LLM
chỉ làm NLU①/NLG⑥** — không chứa business logic, BE là chốt chặn cuối (validate 2 tầng).

Thiết kế đầy đủ: [`../../UC-US-BA-APIDESIGN/detail-design/DD_chatbot.md`](../../UC-US-BA-APIDESIGN/detail-design/DD_chatbot.md)
· kiến trúc: [`../../UC-US-BA-APIDESIGN/chatbot-architecture.md`](../../UC-US-BA-APIDESIGN/chatbot-architecture.md).

## Chạy

```bash
# từ thư mục chatbot/ (dùng chung .venv ở gốc repo)
cp .env.example .env          # điền LLM_* nếu có router; mặc định chạy được không cần gì
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
| `nlg.py` · `templates.py` | Bước⑤⑥ ghép template + sinh câu — CHỈ tiếng Việt (§7) |
| `pii.py` | Mask/unmask/mask_response + Vault (SĐT/email/mã đặt chỗ — §6, Q6) |
| `session.py` | Session + store (TTL sliding 30', rút vault sau 2' — Q5) |
| `shop_api_client.py` | Gọi endpoint GĐ1 như client **public** (giống FE web) — không auth kênh riêng |
| `answers/` | Tủ tra cứu — bảng "loại câu hỏi → gọi API nào" (xem mục dưới) |
| `retrieval.py` · `answers/faq.py` · `data/faq.md` | RAG: BM25 (+ vector/rerank tuỳ chọn) → chốt độ tự tin → sinh câu (xem mục dưới) |
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
  "other" cho `Hải Châu`, "course_price" cho `Gói đầu tiên`) — câu phải có DẤU HIỆU HỎI (`?` hoặc
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

### R — truy xuất, hai backend

Chọn bằng `RAG_BACKEND` trong `.env`:

| | `bm25` (mặc định) | `hybrid` |
|---|---|---|
| Cách tìm | BM25 thuần stdlib | BM25 + vector (Chroma) hợp nhất bằng RRF, xếp lại bằng cross-encoder PhoRanker |
| Cần cài | không gì cả | `chromadb sentence-transformers pyvi` (~2–3GB) |
| Boot | tức thì | ~10–30s nạp model, giữ ~1–1,5GB RAM |
| PII | không có gì rời hệ thống | vẫn không — cả hai model chạy **cục bộ** |

Thiếu gói hoặc tải model hỏng → tự lùi về `bm25` kèm cảnh báo trong log; `GET /health` cho
biết backend **thực tế** đang chạy chứ không phải cái ghi trong `.env`.

Xếp hạng xong còn một chốt nữa là `Retriever._confident`: độ phủ âm tiết nội dung ≥ 0.34
**và** trùng ít nhất một bigram với PHÍA CÂU HỎI của mục (tiêu đề + alias). Không qua chốt →
từ chối, vì bot trả lời tự tin mà sai chủ đề còn tệ hơn nói "em chưa hỗ trợ được".

> **Bài học 26/8 — đừng lặp lại.** Nhánh vector từng có rồi bị gỡ vì đo ra cứu **0/8** câu.
> Nguyên nhân không phải vector kém: ép thẳng chunk ĐÚNG lên hạng 1 (giả lập một nhánh vector
> *hoàn hảo*) thì cả 5 câu **vẫn** bị `_confident` chặn — chốt đó thuần từ vựng và có quyền
> phủ quyết cuối. Lần này nhánh vector đi kèm `_confident(strong=…)`: ứng viên được PhoRanker
> chấm trên `_RERANK_STRONG` được **miễn điều kiện bigram**, nhưng vẫn phải qua điều kiện độ
> phủ. Dựng nhánh mới mà quên nới chốt = lặp lại đúng thất bại cũ.
>
> Phép đo đó còn một điều kiện biên: nó làm trên corpus **21 mục**, nơi BM25 gần như luôn xếp
> đúng hạng 1 (`recall@1` = 100%, chênh `recall@3` = 0%). Ở cỡ đó hybrid **không có gì để
> cứu**. Giá trị của nó chỉ xuất hiện khi corpus phình to — và phải đo lại bằng
> `--backend hybrid` chứ không suy từ kết luận cũ.

### G — sinh câu

Chunk tìm được đi qua LLM diễn đạt lại cho khớp câu khách hỏi (`answers/faq.py::_augment`),
thay vì đọc nguyên văn như trước. Tắt bằng `FAQ_GENERATE=0`; chưa cấu hình router thì tự tắt.

Bước này mang lại ba rủi ro mà bản "trả nguyên văn" vốn miễn nhiễm, nên nó có **sáu hàng
rào**, và mọi hàng rào đều lùi về nguyên văn chunk — hành vi cũ thành lưới đỡ, không bị thay
thế: router lỗi/quá hạn · model in `KHONG_DU_THONG_TIN` · lọt markdown · tự đẻ `{{...}}` ·
lan man quá `2×gốc+120` · cờ tắt. Lý do lùi được in trong khối log của lượt đó.

Chống bịa dựa vào ràng buộc số 1 bên dưới: chunk không chứa số liệu sống thì model không có
gì để chép sai. Chống prompt injection: system prompt tuyên bố `doan_van` là DỮ LIỆU, và
corpus là file review qua git chứ không phải nội dung người lạ.

Ba ràng buộc **không được nới**:

1. **Chỉ chính sách/quy trình vào corpus.** Giờ mở cửa, giá, ngày nghỉ, địa chỉ vẫn phải đi
   qua `shop_api` — dữ liệu sống, ghi vào file là sai ngay hôm sau. Từ khi có bước G thì đây
   còn là ràng buộc **an toàn**, không chỉ gọn gàng.
2. **Truy vấn là text ĐÃ MASK** (`ctx.raw_text`). Trước đây là phòng xa vì retrieval chạy nội
   bộ; từ khi có bước G thì câu hỏi **thật sự** bay sang router, nên chốt này thành bắt buộc.
3. **Corpus review qua git.** Đừng làm bảng cho staff sửa trong admin: ai sửa được file là
   nói thay bot được.

### Corpus và thước đo

Corpus là `data/faq.md`, **hoặc** cả thư mục `data/faq/` nếu nó tồn tại (kho vài trăm mục
trong một file thì không review nổi qua git). Mỗi chunk nhớ file nguồn của nó, in kèm trong
log để biết câu sai đến từ đâu.

Sửa corpus xong thì chạy:

```bash
python tests/check_faq.py                     # bảng đối chiếu + 3 chỉ số
python tests/check_faq.py --backend hybrid    # so kèo với BM25 thuần
python tests/check_faq.py --backend hybrid --calibrate   # dò ngưỡng _RERANK_STRONG
python tests/check_faq_gen.py                 # đo chữ G (cần router)
```

Ba chỉ số ở cuối `check_faq.py`, và khoảng cách giữa hai cái đầu mới là thứ đáng đọc:

- **`recall@3 − recall@1`** — mục đúng đã tìm ra nhưng xếp sai hạng. Đây đúng là phần việc
  reranker làm được, và **chỉ chừng đó thôi**. Chênh 0% thì đừng bật hybrid.
- **từ chối oan** — đúng hạng 1 mà `_confident` chặn. Cao thì vấn đề ở chốt chặn, nới
  `_confident` mới đúng thuốc; thêm nhánh mới là vô ích (bài học 26/8).

Danh sách `MUST_REJECT` quan trọng ngang `MUST_ANSWER` và **phải giữ 10/10 ở mọi backend,
mọi ngưỡng** — bot trả lời tự tin nhưng lạc chủ đề tệ hơn mọi cải thiện recall cộng lại.
Không nhận ra câu nào thì thêm dòng `> ` vào mục tương ứng: rẻ hơn mọi thứ khác trong trang này.

## Đọc log

`logs/chatbot.log` — mỗi lượt chat là MỘT khối, đọc từ trên xuống thấy đủ input → xử lý → output:

```
┌─ LƯỢT 3 · conv=demo · vào state=GREETING
│ IN     Cửa hàng Hải Châu
│ ①NLU   rule_based 0.00s · intent=book · qt=-      ← "(bỏ qua — …)" nếu nhánh không qua NLU
│ LANE   TASK                         ← điền đơn / QUERY (hỏi) / META (chào, sửa, hủy)
│ ②GỘP   shop_text: None→'Cửa hàng Hải Châu'
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
  người. Một câu nêu nhiều add-on ("Bấm huyệt bàn chân với Đá nóng") nhận hết, khớp bằng
  `matching.pick_all` (không phải `pick_unique` — 2 tên khớp không phải là "mơ hồ" ở đây).
  Danh sách gói/add-on đọc ra đều **đánh số**, nên khách trả lời bằng số cũng nhận
  (`matching.pick_by_index`).
- **Khớp tên hạ dần 3 tầng** (`matching.pick_unique`): chuỗi-con → theo TỪ (`"Sài Gòn đi"`) →
  khớp MỜ chịu lỗi gõ (`"Massge body 120"` → `Massage body 120`). Mơ hồ ở tầng nào cũng dừng và
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
