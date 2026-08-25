"""Tủ tra cứu — bảng "loại câu hỏi -> gọi API nào". CHỈ ĐỌC, không ghi vào tờ đơn.

Thêm loại câu hỏi mới = thêm một dòng vào RESOLVERS; luồng đặt lịch không đổi. Đây cũng là
chỗ cắm FAQ/RAG sau này (câu hỏi mà đáp án nằm trong văn bản, không nằm trong bảng nào).
"""

from __future__ import annotations

import logging

from app.answers import location, shop_info
from app.answers.base import NOT_RESOLVED, Answer, QueryCtx
from app.shop_api_client import ShopApiError

logger = logging.getLogger(__name__)

RESOLVERS = {
    "shops_open_at": shop_info.shops_open_at,
    "shop_contact": shop_info.shop_contact,
    "shop_days_off": shop_info.shop_days_off,
    "course_price": shop_info.course_price,
    "shops_near": location.shops_near,
}


# Loại câu hỏi mà LÀN QUERY nhận xử lý. "other" không có resolver nhưng vẫn phải vào đây:
# khách hỏi chuyện ngoài phạm vi thì đáng được câu "em chưa hỗ trợ được" + đọc lại câu đang
# dở, chứ không phải "em chưa rõ ý anh/chị" của nhánh REPROMPT.
HANDLED = set(RESOLVERS) | {"other"}


def resolve(ctx: QueryCtx, api) -> Answer:
    """Tra bảng rồi chạy resolver. Lỗi API bị NUỐT ở đây thành NOT_RESOLVED: một câu hỏi
    hỏng tuyệt đối không được ném ngoại lệ ra handle_turn và cắt ngang phiên đặt lịch."""
    fn = RESOLVERS.get(ctx.question_type)
    if fn is None:
        return NOT_RESOLVED
    try:
        return fn(ctx, api)
    except ShopApiError as e:
        logger.warning("answers: %s lỗi shop_api (%s) -> xin lỗi, hỏi lại", ctx.question_type, e)
        return NOT_RESOLVED


__all__ = ["RESOLVERS", "HANDLED", "resolve", "Answer", "QueryCtx", "NOT_RESOLVED"]
