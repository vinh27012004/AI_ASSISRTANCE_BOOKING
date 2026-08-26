"""Resolver FAQ — câu hỏi mà đáp án nằm trong VĂN BẢN, không nằm trong bảng nào.

Đây là chỗ `answers/__init__.py` đã chừa sẵn từ đầu. Khác 7 resolver kia ở nguồn dữ liệu:
chúng gọi `shop_api` lấy số liệu sống (giờ, giá, ngày nghỉ), còn cái này tra
`data/faq.md` — chính sách/quy trình, thứ không có endpoint nào trả.

Ba quyết định đáng chú ý:

1. **RAG không có G.** Chunk tìm được trả cho khách NGUYÊN VĂN, không nhờ LLM viết lại.
   Đổi lại ba thứ: không bịa, không thêm một lượt gọi LLM vào mỗi câu hỏi, và bịt luôn
   đường prompt injection gián tiếp (chunk không bao giờ vào prompt nên chunk có viết
   "bỏ qua hướng dẫn trước" cũng vô hại). Giá phải trả là câu trả lời cứng theo văn bản
   soạn sẵn — chấp nhận được, vì `INFO` vốn đã nằm trong `_LITERAL_SAFE_KEYS` của nlg.py.

2. **Truy vấn dùng text ĐÃ MASK** (`ctx.raw_text`, xem docstring của `QueryCtx`). Bật
   nhánh vector là câu truy vấn bay sang nhà cung cấp embedding — PII phải được thay bằng
   placeholder TRƯỚC đó. Placeholder không ảnh hưởng chất lượng tìm kiếm vì câu hỏi chính
   sách gần như không chứa số điện thoại/email.

3. **Lưới hứng cuối, không phải resolver ngang hàng.** `resolve()` gọi nó khi mọi resolver
   khác đã chê — nhờ vậy thêm câu hỏi mới chỉ cần sửa `data/faq.md`, không phải dạy thêm
   luật cho `nlu._detect_question`.
"""

from __future__ import annotations

import logging

from app.answers.base import NOT_RESOLVED, Answer, QueryCtx

logger = logging.getLogger(__name__)

# Đặt bởi Orchestrator lúc khởi tạo (chỗ duy nhất cầm Settings). None -> FAQ tắt, mọi thứ
# chạy y như trước khi có module này.
_RETRIEVER = None


def configure(retriever) -> None:
    global _RETRIEVER
    _RETRIEVER = retriever


def is_ready() -> bool:
    return _RETRIEVER is not None and bool(getattr(_RETRIEVER, "chunks", None))


def answer(ctx: QueryCtx, api) -> Answer:
    """`api` không dùng — FAQ không gọi shop_api. Giữ tham số cho khớp chữ ký RESOLVERS."""
    if not is_ready():
        return NOT_RESOLVED

    query = (ctx.raw_text or "").strip()
    if not query:
        return NOT_RESOLVED

    hits = _RETRIEVER.search(query, top_k=1)
    if not hits:
        logger.info("faq: không mục nào đủ tự tin cho %r", query)
        return NOT_RESOLVED

    chunk, score = hits[0]
    logger.info("faq: %r -> %r (rrf=%.4f)", query, chunk.title, score)
    # KHÔNG trả `suggest`: mục FAQ là văn bản tĩnh, không biết gì về phiên hiện tại nên
    # không có quyền điền vào tờ đơn. Tra cứu thuần.
    return Answer(text=chunk.answer_text)
