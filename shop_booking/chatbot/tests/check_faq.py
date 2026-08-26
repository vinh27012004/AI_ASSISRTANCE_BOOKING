"""Soi chất lượng retrieval FAQ — chạy sau mỗi lần sửa data/faq.md.

    python tests/check_faq.py            # bảng đối chiếu
    python tests/check_faq.py "câu hỏi"  # tra một câu, xem top 3

Khác test_chatbot.py ở mục đích: bên kia là cổng CI (đúng/sai), đây là kính lúp — in ra
mục nào được chọn để người viết FAQ biết nên thêm dòng '> ' vào đâu.

MUST_REJECT quan trọng ngang MUST_ANSWER: bot trả lời tự tin nhưng sai chủ đề còn tệ hơn
bot nói "em chưa hỗ trợ được".
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.CRITICAL)

from app import retrieval
from app.config import load_settings

# (câu hỏi, phần chữ phải có trong tiêu đề mục được chọn)
MUST_ANSWER = [
    ("hủy lịch có mất phí không", "Hủy lịch"),
    ("tôi muốn hủy đặt chỗ", "Hủy lịch"),
    ("tôi muốn dời sang hôm khác", "Đổi lịch"),
    ("đổi lịch sang ngày khác được không", "Đổi lịch"),
    ("sát giờ rồi còn hủy được không", "Sát giờ hẹn"),
    ("vừa đặt xong muốn sửa ngay", "Vừa đặt xong"),
    ("tôi muốn đổi sang chi nhánh khác", "Đổi sang cửa hàng khác"),
    ("đi 4 người có được không", "tối đa mấy người"),
    ("đặt cho nhóm đông người được không", "tối đa mấy người"),
    ("nhóm 2 người chỉ định được nhân viên nữ không", "Đi nhóm có chỉ định"),
    ("mỗi người một dịch vụ khác nhau được không", "chọn dịch vụ khác nhau"),
    ("tôi muốn chọn nhân viên phục vụ", "Chỉ định nhân viên"),
    ("add on đặt riêng được không", "Add-on đặt riêng"),
    ("vì sao không chọn được add on", "add-on không chọn được"),
    ("mã đặt chỗ gửi ở đâu", "Mã đặt chỗ gửi ở đâu"),
    ("tôi quên mã đặt chỗ rồi", "Quên hoặc mất mã đặt chỗ"),
    ("tại sao phải cho số điện thoại", "cần số điện thoại"),
    ("hệ thống báo từ chối không đặt được", "báo từ chối"),
    ("hạng thành viên có giảm giá không", "hạng thành viên"),
    ("đặt trong ngày được không", "đặt trong ngày"),
    ("vì sao giờ vừa chọn báo hết chỗ", "báo hết chỗ"),
    ("tôi đến muộn 15 phút thì sao", "Đến muộn"),
    ("gói kéo dài bao lâu", "Thời lượng dịch vụ"),
    ("cho tôi gặp người thật", "gặp nhân viên tư vấn"),
]

# Phải TỪ CHỐI: hoặc ngoài phạm vi, hoặc là dữ liệu SỐNG (phải đi qua shop_api).
MUST_REJECT = [
    "có chỗ đỗ xe không",
    "có wifi không",
    "nhận thanh toán bằng thẻ không",
    "thời tiết hôm nay thế nào",
    "cho tôi đặt lịch",
    "shop A",
    "hôm nay mấy giờ đóng cửa",
    "gói toàn thân giá bao nhiêu",
    "cửa hàng nào gần Hải Châu",
    "chủ nhật này có nghỉ không",
]


def main() -> int:
    r = retrieval.build_retriever(load_settings())
    print(f"corpus: {len(r.chunks)} mục · nhánh vector: "
          f"{'bật' if r.embedder and r.vectors else 'tắt (BM25-only)'}\n")

    if len(sys.argv) > 1:
        q = " ".join(sys.argv[1:])
        hits = r.search(q, top_k=3)
        print(f"{q!r}")
        for c, s in hits or []:
            print(f"   {s:.5f}  {c.title}")
        if not hits:
            print("   (từ chối — không đủ tự tin)")
        return 0

    bad = 0
    print("── phải trả lời được " + "─" * 45)
    for q, want in MUST_ANSWER:
        hits = r.search(q, top_k=1)
        got = hits[0][0].title if hits else None
        ok = got is not None and want.lower() in got.lower()
        bad += not ok
        print(f"  {'ok  ' if ok else 'SAI '} {q!r}\n       -> {got or '(từ chối)'}"
              + ("" if ok else f"\n       mong đợi tiêu đề chứa {want!r}"))

    print("\n── phải từ chối " + "─" * 50)
    for q in MUST_REJECT:
        hits = r.search(q, top_k=1)
        ok = not hits
        bad += not ok
        print(f"  {'ok  ' if ok else 'SAI '} {q!r}"
              + ("" if ok else f"\n       -> trót trả lời bằng {hits[0][0].title!r}"))

    total = len(MUST_ANSWER) + len(MUST_REJECT)
    print(f"\n{total - bad}/{total} đúng." + ("" if not bad else f"  {bad} câu cần chỉnh."))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
