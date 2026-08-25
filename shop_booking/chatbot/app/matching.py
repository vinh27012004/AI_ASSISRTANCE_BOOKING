"""Khớp tên khách nói với tên trong dữ liệu (shop/course/add-on/nhân viên).

Tách khỏi orchestrator.py để `answers/` dùng lại được mà không vòng import
(orchestrator import answers, nên answers KHÔNG được import orchestrator).
"""

from __future__ import annotations


def name_matches(query: str, name: str) -> bool:
    """Khớp nguyên văn, hoặc chuỗi-con HAI CHIỀU nhưng chỉ khi query đủ dài (≥3 ký tự) —
    input 1-2 ký tự ("a", "to") là chuỗi con của quá nhiều tên, dễ trúng bừa mục đầu tiên
    có chữ đó. Query ngắn chỉ nhận khi bằng đúng MỘT TỪ trong tên."""
    q = (query or "").strip().lower()
    n = (name or "").strip().lower()
    if not q or not n:
        return False
    if q == n:
        return True
    if len(q) >= 3:
        return q in n or n in q
    return q in n.split()


def pick_unique(query: str, items: list[dict]) -> dict | None:
    """Item DUY NHẤT có tên khớp query; 0 hoặc ≥2 item khớp -> None. Mơ hồ thì hỏi lại chứ
    không chọn bừa cái đầu tiên — vd "cửa hàng" là chuỗi con của MỌI tên cửa hàng."""
    hits = [it for it in items if name_matches(query, it.get("name") or "")]
    return hits[0] if len(hits) == 1 else None


def pick_all(query: str, items: list[dict]) -> list[dict]:
    """MỌI item có tên xuất hiện trong câu khách nói — dùng cho chỗ chọn NHIỀU (add-on).

    Khác pick_unique ở hai điểm, và cả hai đều cần thiết:
    - Chỉ khớp MỘT CHIỀU (tên nằm trong câu). Câu "cho tôi Foot với Hot Stone" chứa hai
      tên; pick_unique thấy 2 kết quả sẽ coi là mơ hồ rồi bỏ sạch — đó là lý do trước đây
      chat chỉ nhận được một add-on trong khi web tick được nhiều.
    - Tên ngắn bị BAO trong tên dài hơn cũng khớp thì bị loại, giữ tên dài ("Aroma Oil 90"
      thắng "Aroma Oil"), tránh nhận nhầm thành hai dịch vụ.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    def _n(it: dict) -> str:
        return (it.get("name") or "").strip().lower()

    hits = [it for it in items if len(_n(it)) >= 3 and _n(it) in q]
    return [it for it in hits
            if not any(_n(it) != _n(o) and _n(it) in _n(o) for o in hits)]
