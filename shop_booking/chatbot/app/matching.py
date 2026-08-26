"""Khớp tên khách nói với tên trong dữ liệu (shop/course/add-on/nhân viên).

Tách khỏi orchestrator.py để `answers/` dùng lại được mà không vòng import
(orchestrator import answers, nên answers KHÔNG được import orchestrator).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


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


# Từ không phân biệt được mục nào với mục nào -> bỏ khi khớp theo TỪ.
_TOKEN_STOP = {"cửa", "hàng", "cua", "hang", "shop", "quán", "quan", "chi", "nhánh",
               "gói", "goi", "cho", "tôi", "toi", "của", "phút", "phut", "dịch", "vụ"}


def _tokens(text: str) -> set[str]:
    return {t for t in _norm(text).split() if len(t) >= 3 and t not in _TOKEN_STOP}


# Khớp MỜ — chịu được gõ sai vài ký tự ("Massge body 120" -> "Massage body 120"). Chỉ chạy
# khi khớp chính xác KHÔNG ra kết quả nào, và phải có ứng viên thắng RÕ RÀNG.
_FUZZY_MIN_LEN = 4        # query ngắn hơn thì mọi thứ đều "hao hao", không đoán
_FUZZY_MIN = 0.78         # độ giống tối thiểu
_FUZZY_MARGIN = 0.06      # phải hơn ứng viên nhì bấy nhiêu ("Massage body" hoà giữa 30/60/90 -> bỏ)
_NORM_RE = re.compile(r"[^0-9a-zà-ỹ]+")


def _norm(text: str) -> str:
    return _NORM_RE.sub(" ", (text or "").lower()).strip()


def _ratio(q: str, item: dict) -> float:
    """Lấy điểm CAO NHẤT giữa so-với-tên-đầy-đủ và so-với-phần-đặc-trưng.

    Phần chung ("Cửa hàng ...") có mặt ở mọi tên nên nó pha loãng điểm: "hoàn kiêm" so
    với cả cụm "cửa hàng hoàn kiếm" thì trượt ngưỡng, nhưng so với "hoàn kiếm" là 0.89."""
    name = item.get("name") or ""
    core = " ".join(sorted(_tokens(name)))
    best = SequenceMatcher(None, q, _norm(name)).ratio()
    if core:
        best = max(best, SequenceMatcher(None, q, core).ratio())
    return best


def _pick_fuzzy(query: str, items: list[dict]) -> dict | None:
    q = _norm(query)
    if len(q) < _FUZZY_MIN_LEN:
        return None
    scored = sorted(
        ((_ratio(q, it), i, it) for i, it in enumerate(items)),
        key=lambda x: (-x[0], x[1]),
    )
    if not scored or scored[0][0] < _FUZZY_MIN:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < _FUZZY_MARGIN:
        return None                       # hai ứng viên ngang nhau -> mơ hồ, hỏi lại
    return scored[0][2]


def _pick_by_token(query: str, items: list[dict]) -> dict | None:
    """Khớp khi query và tên có CHUNG một từ đặc trưng — "Sài Gòn đi" -> "Cửa hàng Sài
    Gòn". Chỉ chạy sau khi khớp chuỗi-con không ra kết quả nào, nên không đụng tới các
    ca đã khớp đúng ("Massage body 120" vẫn ra đúng mức 120 qua chuỗi con)."""
    q = _tokens(query)
    if not q:
        return None
    hits = [it for it in items if q & _tokens(it.get("name") or "")]
    return hits[0] if len(hits) == 1 else None


def pick_unique(query: str, items: list[dict]) -> dict | None:
    """Item DUY NHẤT có tên khớp query; 0 hoặc ≥2 item khớp -> None. Mơ hồ thì hỏi lại chứ
    không chọn bừa cái đầu tiên — vd "cửa hàng" là chuỗi con của MỌI tên cửa hàng.

    Không khớp chuỗi-con được thì hạ dần: khớp theo TỪ ("Sài Gòn đi"), rồi khớp MỜ
    (gõ sai chính tả — "Massge body 120")."""
    hits = [it for it in items if name_matches(query, it.get("name") or "")]
    if len(hits) == 1:
        return hits[0]
    if hits:
        return None                       # nhiều kết quả = mơ hồ thật, đừng đoán tiếp
    return _pick_by_token(query, items) or _pick_fuzzy(query, items)


# Từ báo hiệu "đang nói tới MỤC SỐ MẤY", không phải một con số bất kỳ.
_INDEX_CUE = r"(?:thứ|thu|cái|cai|số|so|gói|goi|mục|muc|phần|phan|option)"
# Đơn vị đo — số đứng trước mấy chữ này là số lượng/thời lượng, KHÔNG phải số thứ tự.
# Thiếu chốt này thì "gói cho 2 người" bị đọc thành "mục số 2".
_MEASURE = (r"(?:người|nguoi|ng|phút|phut|giờ|gio|tiếng|tieng|ngày|ngay"
            r"|đồng|dong|₫|đ|vnd|nghìn|nghin|triệu|trieu|k)")

# Dạng dài: có từ chỉ mục, cho phép TỐI ĐA MỘT chữ đệm giữa nó và con số ("cái thứ 4",
# và cả lỗi gõ "cái thú 4"), và con số phải ở gần CUỐI câu — chọn theo số thứ tự bao giờ
# cũng là lời kết, không phải mệnh đề giữa câu.
_INDEX_PHRASE_RE = re.compile(
    rf"\b{_INDEX_CUE}\b(?:\s+\w+)?\s*(\d{{1,2}})\b(?!\s*{_MEASURE}\b)[^0-9]{{0,8}}$"
)


def pick_by_index(query: str, items: list[dict]) -> dict | None:
    """Khách trả lời bằng SỐ THỨ TỰ trong danh sách bot vừa đọc ("2", "gói 3", "cái thứ 4").

    Hai mẫu, thử từ chặt tới rộng:

    1. Gần như chỉ có con số ("4", "gói 4", "2 đi") — giới hạn 6 ký tự đệm mỗi bên để
       không nuốt nhầm số trong câu dài.
    2. Có TỪ CHỈ MỤC dẫn đường ("cái thứ 4", "chọn số 2 nhé") — mẫu 1 bó tay ở đây vì
       "Tôi chọn cái thứ 4" có tới 14 ký tự trước con số, và đó là cách nói tự nhiên
       nhất; log thật cho thấy khách lặp lại hai lượt liền mà bot vẫn đọc lại danh sách.
    """
    q = (query or "").strip()
    m = (re.fullmatch(r"[^0-9]{0,6}?(\d{1,2})[^0-9]{0,6}", q)
         or _INDEX_PHRASE_RE.search(q.lower()))
    if not m:
        return None
    idx = int(m.group(1)) - 1
    return items[idx] if 0 <= idx < len(items) else None


def pick_all(query: str, items: list[dict]) -> list[dict]:
    """MỌI item có tên xuất hiện trong câu khách nói — dùng cho chỗ chọn NHIỀU (add-on).

    Khác pick_unique ở hai điểm, và cả hai đều cần thiết:
    - Chỉ khớp MỘT CHIỀU (tên nằm trong câu). Câu "cho tôi Bấm huyệt với Đá nóng" chứa hai
      tên; pick_unique thấy 2 kết quả sẽ coi là mơ hồ rồi bỏ sạch — đó là lý do trước đây
      chat chỉ nhận được một add-on trong khi web tick được nhiều.
    - Tên ngắn bị BAO trong tên dài hơn cũng khớp thì bị loại, giữ tên dài (add-on "Đá
      nóng" nằm gọn trong "Đá nóng toàn thân" thì chỉ nhận cái dài), tránh nhận nhầm
      thành hai dịch vụ.
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    def _n(it: dict) -> str:
        return (it.get("name") or "").strip().lower()

    hits = [it for it in items if len(_n(it)) >= 3 and _n(it) in q]
    return [it for it in hits
            if not any(_n(it) != _n(o) and _n(it) in _n(o) for o in hits)]
