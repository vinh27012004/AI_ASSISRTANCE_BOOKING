"""Kiểu dữ liệu của LÀN QUERY (tủ tra cứu) — khách HỎI thông tin, không phải điền đơn.

Resolver KHÔNG cầm Session: nó chỉ nhận QueryCtx (bản chụp chỉ-đọc) nên không thể ghi vào
tờ đơn. Muốn điền ô thì trả Answer.suggest để orchestrator đưa qua sm.merge_params — giữ
MỘT cửa ghi duy nhất, nhờ đó _invalidate (BR-04/BR-07) vẫn chạy đủ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class QueryCtx:
    question_type: str
    entities: dict                      # entity NLU trích được ở lượt này
    shop_id: Optional[int] = None       # shop đang chọn trong phiên (nếu có)
    date: Optional[str] = None
    party_size: Optional[int] = None
    # Khách nói "7h" mà không kèm buổi -> mơ hồ, phải trả lời cả sáng lẫn tối.
    time_ambiguous: bool = False
    # Câu gốc (đã mask PII). Chỉ dùng để khớp khu vực khi NLU không trích được `location`
    # — nhánh rule-based offline không biết tên địa danh nên hay để trống.
    raw_text: str = ""
    # Danh sách shop_id của câu trả lời TRƯỚC. Cho phép khách hỏi tiếp kiểu "trong 2 cửa
    # hàng đó cái nào có nhân viên nữ" mà không phải nhắc lại tên.
    shortlist: tuple[int, ...] = ()
    # Gói dịch vụ khách ĐÃ chốt trong tờ đơn — theo TÊN, không theo id, vì id course/add-on
    # là riêng của từng cửa hàng. Có nó thì câu "cửa hàng nào mở lúc 19h?" mới trả lời được
    # theo đúng nghĩa "chỗ nào NHẬN ĐƯỢC gói này lúc 19h" (xem shop_info.shops_open_at).
    course_name: Optional[str] = None
    addon_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Answer:
    text: str = ""                      # câu trả lời soạn TẤT ĐỊNH (chứa số liệu THẬT)
    resolved: bool = True               # False -> bot xin lỗi + hỏi lại câu đang dở
    suggest: dict = field(default_factory=dict)   # entity dạng NLU -> merge_params
    # shop_id mà câu trả lời này nêu ra — orchestrator ghi lại làm ngữ cảnh cho lượt sau.
    shortlist: tuple[int, ...] = ()


NOT_RESOLVED = Answer(resolved=False)
