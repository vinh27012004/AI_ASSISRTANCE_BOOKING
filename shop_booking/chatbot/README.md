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
| `buttons.py` | Nút lựa chọn shop/course/slot… (§7) |
| `main.py` · `wsgi.py` | Flask `POST /chat/message` (§2.1, Q3) |

## Quan hệ với BE

Chatbot dùng lại **nguyên bộ API GĐ1** như một client public (giống FE web) — **không** cần thay
đổi gì ở `shop_api`. Chống bấm đúp do BE tự lo bằng dedup thời gian 120s (cùng khách + shop +
ngày + giờ → trả lại booking cũ).

> Trước đây từng cân nhắc thêm API key kênh (`X-Api-Key`) + `Idempotency-Key`, nhưng đã **bỏ**
> theo yêu cầu mentor: chatbot chỉ gọi API bình thường.

## Luồng bước (đã tinh chỉnh)

`SHOP → DATE → PARTY_SIZE → COURSE → ADDON → THERAPIST(1 người) → SLOT → CONTACT → CONFIRM → CREATE → DONE`

- **Bỏ bước hỏi thời lượng**: mỗi course đã kèm sẵn `duration_min` (hiện luôn trên nút).
- **COURSE và ADDON là hai bước riêng**: chọn course chính trước, rồi chọn add-on (toggle) hoặc "Không thêm".
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
