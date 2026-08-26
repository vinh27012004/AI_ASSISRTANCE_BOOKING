"""Tủ tra cứu — bảng "loại câu hỏi -> gọi API nào". CHỈ ĐỌC, không ghi vào tờ đơn.

Thêm loại câu hỏi mới = thêm một dòng vào RESOLVERS; luồng đặt lịch không đổi. Đây cũng là
chỗ cắm FAQ/RAG sau này (câu hỏi mà đáp án nằm trong văn bản, không nằm trong bảng nào).
"""

from __future__ import annotations

import logging
from dataclasses import replace

from app.answers import faq, location, shop_info
from app.answers.base import NOT_RESOLVED, Answer, QueryCtx
from app.shop_api_client import ShopApiError

logger = logging.getLogger(__name__)

RESOLVERS = {
    "shops_open_at": shop_info.shops_open_at,
    "shop_contact": shop_info.shop_contact,
    "shop_days_off": shop_info.shop_days_off,
    "course_price": shop_info.course_price,
    "shops_near": location.shops_near,
    "shops_list": shop_info.shops_list,
    "shops_by_staff": shop_info.shops_by_staff,
    # Tra văn bản (data/faq.md) thay vì gọi shop_api. Xem app/answers/faq.py.
    "faq": faq.answer,
}


# Loại câu hỏi mà LÀN QUERY nhận xử lý. "other" không có resolver nhưng vẫn phải vào đây:
# khách hỏi chuyện ngoài phạm vi thì đáng được câu "em chưa hỗ trợ được" + đọc lại câu đang
# dở, chứ không phải "em chưa rõ ý anh/chị" của nhánh REPROMPT.
HANDLED = set(RESOLVERS) | {"other"}


def reload_corpus(retriever) -> None:
    """Nạp lại retriever FAQ (dùng khi sửa data/faq.md lúc dev)."""
    faq.configure(retriever)


def resolve(ctx: QueryCtx, api) -> Answer:
    """Tra bảng rồi chạy resolver. Lỗi API bị NUỐT ở đây thành NOT_RESOLVED: một câu hỏi
    hỏng tuyệt đối không được ném ngoại lệ ra handle_turn và cắt ngang phiên đặt lịch."""
    fn = RESOLVERS.get(ctx.question_type)
    if fn is None:
        # LLM hay gán "other" cho cả câu hỏi ta trả lời được ("bên mình có cửa hàng nào?").
        # Thử lại bằng luật trước khi bó tay — thà tra cứu thừa còn hơn xin lỗi oan.
        from app import nlu
        guess = nlu._detect_question((ctx.raw_text or "").lower())
        fn = RESOLVERS.get(guess) if guess else None
        if fn is None:
            # LƯỚI HỨNG CUỐI: không luật nào nhận -> thử tra văn bản FAQ. Đây là lý do
            # thêm câu hỏi mới chỉ cần sửa data/faq.md, không phải viết thêm resolver
            # lẫn thêm luật vào _detect_question. Không đủ tự tin thì faq.answer tự trả
            # NOT_RESOLVED, bot xin lỗi như cũ.
            if ctx.question_type != "faq" and faq.is_ready():
                return _run(faq.answer, replace(ctx, question_type="faq"), api)
            return NOT_RESOLVED
        logger.info("answers: qt=%r không có resolver -> suy theo luật thành %r",
                    ctx.question_type, guess)
        ctx = replace(ctx, question_type=guess)
    return _run(fn, ctx, api)


def _run(fn, ctx: QueryCtx, api) -> Answer:
    try:
        return fn(ctx, api)
    except ShopApiError as e:
        logger.warning("answers: %s lỗi shop_api (%s) -> xin lỗi, hỏi lại", ctx.question_type, e)
        return NOT_RESOLVED


__all__ = ["RESOLVERS", "HANDLED", "resolve", "Answer", "QueryCtx", "NOT_RESOLVED"]
