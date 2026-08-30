"""Soi chất lượng retrieval FAQ — chạy sau mỗi lần sửa data/faq.md.

    python tests/check_faq.py                      # bảng đối chiếu + chỉ số
    python tests/check_faq.py "câu hỏi"            # tra một câu, xem top 3
    python tests/check_faq.py --backend hybrid     # so kèo với BM25 thuần
    python tests/check_faq.py --backend hybrid --calibrate   # dò ngưỡng _RERANK_STRONG

Khác test_chatbot.py ở mục đích: bên kia là cổng CI (đúng/sai), đây là kính lúp — in ra
mục nào được chọn để người viết FAQ biết nên thêm dòng '> ' vào đâu.

MUST_REJECT quan trọng ngang MUST_ANSWER: bot trả lời tự tin nhưng sai chủ đề còn tệ hơn
bot nói "em chưa hỗ trợ được".

BA CHỈ SỐ in ở cuối, và khoảng cách giữa hai cái đầu mới là thứ đáng đọc:

- recall@1  — hạng 1 đúng mục (tính TRƯỚC chốt _confident)
- recall@3  — mục đúng nằm trong top 3
- từ chối oan — câu đúng chủ đề nhưng bị _confident chặn

`recall@3 - recall@1` đo đúng phần việc mà một reranker làm được: mục đúng đã tìm thấy rồi,
chỉ xếp sai hạng. Khoảng cách nhỏ thì rerank không có gì để sửa, đừng tốn công.
`từ chối oan` cao thì vấn đề nằm ở chốt chặn chứ không ở xếp hạng — nới _confident mới đúng
thuốc, thêm nhánh mới là vô ích (đây chính là bài học ngày 26/8).
"""

import logging
import os
import sys
from dataclasses import replace

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
    # --- các mục thêm ngày 28/8 khi kho lên 82 mục. Câu dò cố ý diễn đạt KHÁC tiêu đề:
    # trùng chữ với tiêu đề thì phép đo chỉ chứng minh BM25 biết so chuỗi, không chứng minh
    # khách hỏi tự nhiên là tra được.
    ("nói một lần hết thông tin có được không", "nhiều thông tin"),
    ("đang đặt mà muốn hỏi thêm chuyện khác", "hỏi chuyện khác"),
    ("tôi đặt giúp cho vợ tôi được không", "hộ"),
    ("hai buổi khác hôm đặt chung một lần được không", "nhiều lịch"),
    ("nhóm bốn người thì sao", "liên hệ cửa hàng"),
    ("cả nhóm có phải vào cùng lúc không", "cùng giờ"),
    ("đi hai người mà chỉ một người làm", "một người làm"),
    ("thêm một người nữa sau khi đã đặt", "thêm người"),
    ("gói chính với phần thêm khác nhau chỗ nào", "gói chính"),
    ("tôi không muốn thêm phần nào cả", "muốn thêm"),
    ("trả lời bằng số thứ tự được không", "số thứ tự"),
    ("buổi làm kéo dài tính sao", "thời lượng"),
    ("xin kỹ thuật viên nữ được không", "nam hay nữ"),
    ("để cửa hàng tự xếp người cũng được", "không quan tâm"),
    ("chỉ định người xong thì ít khung giờ hơn", "ít giờ trống"),
    ("muốn đổi sang người khác sau khi đặt rồi", "đổi sang nhân viên"),
    ("sau khi đặt thì sửa được những mục nào", "được sửa"),
    ("vì sao lại hỏi email lúc tôi sửa", "nhập lại email"),
    ("hủy rồi tôi đặt lại được chứ", "đặt lại"),
    ("sửa xong thì mã có thay đổi không", "mã mới"),
    ("không có email thì sao", "không có email"),
    ("thư báo mã chưa thấy đâu", "không nhận được email"),
    ("thông tin của tôi dùng vào việc gì", "dùng vào việc gì"),
    ("trợ lý này giúp được những gì", "làm được"),
    ("lỡ gửi trùng hai lần có sao không", "hai lần"),
    ("tôi muốn phản ánh chất lượng", "góp ý"),
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


def _build(backend: str | None):
    """Dựng retriever, ép backend nếu dòng lệnh chỉ định (không phải sửa .env để so kèo)."""
    settings = load_settings()
    if backend:
        settings = replace(settings, rag_backend=backend)
    return retrieval.build_retriever(settings)


def _rank_of(r, query: str, want: str) -> int | None:
    """Hạng của mục ĐÚNG trong danh sách ứng viên, BỎ QUA chốt _confident. None = không có
    trong danh sách. Tách khỏi search() để phân biệt 'xếp sai hạng' với 'bị chặn oan'."""
    for rank, (idx, _score, _strong) in enumerate(r._candidates(query)):
        if want.lower() in r.chunks[idx].title.lower():
            return rank
    return None


def _calibrate(r) -> int:
    """In phân bố điểm cross-encoder cho câu ĐÚNG và câu PHẢI TỪ CHỐI. Ngưỡng
    Retriever._RERANK_STRONG nên đặt CAO HƠN mọi điểm ở cột phải."""
    if not r.is_hybrid or r.reranker is None:
        print("--calibrate cần --backend hybrid và gói rerank đã cài.")
        return 1

    def top_score(q):
        cands = r._candidates(q)
        return cands[0][1] if cands else 0.0

    good = sorted(top_score(q) for q, _ in MUST_ANSWER)
    bad = sorted((top_score(q) for q in MUST_REJECT), reverse=True)
    print("── điểm rerank của hạng 1 " + "─" * 40)
    print(f"  câu ĐÚNG    — thấp nhất {good[0]:.3f} · trung vị {good[len(good) // 2]:.3f}")
    print(f"  câu TỪ CHỐI — cao nhất  {bad[0]:.3f} · trung vị {bad[len(bad) // 2]:.3f}")
    # Quét ngưỡng, thay vì chỉ so min(đúng) với max(từ chối). Đòi hai nhóm tách HOÀN TOÀN là
    # quá khắt khe: câu đúng nào bị chấm thấp thì chỉ mất phần MIỄN chốt bigram, nó vẫn đi
    # đường cũ — không mất câu trả lời. Thứ tuyệt đối không được xảy ra là câu TỪ CHỐI lọt.
    print("\n  ngưỡng   TỪ CHỐI lọt   ĐÚNG được miễn chốt bigram")
    best = None
    for t in (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        leak = sum(v >= t for v in bad)
        gain = sum(v >= t for v in good)
        flag = ""
        if leak == 0 and (best is None or gain > best[1]):
            best, flag = (t, gain), "   ← tốt nhất"
        print(f"    {t:.1f}      {leak:>2}/{len(bad)}          {gain:>3}/{len(good)}{flag}")

    if best:
        print(f"\n  => đặt _RERANK_STRONG = {best[0]:.1f}: không câu từ chối nào lọt, "
              f"{best[1]}/{len(good)} câu đúng được miễn chốt")
    else:
        print("\n  => mọi ngưỡng đều để lọt câu phải từ chối. ĐỪNG bật miễn chốt.")
    print(f"  đang đặt: _RERANK_STRONG = {retrieval.Retriever._RERANK_STRONG}")
    print("\n  Lưu ý: chừng nào 'từ chối oan' còn bằng 0 thì việc miễn chốt CHƯA cứu được gì"
          "\n  — không có câu đúng nào đang bị chặn để mà cứu.")
    return 0


def main() -> int:
    args = [a for a in sys.argv[1:]]
    calibrate = "--calibrate" in args
    if calibrate:
        args.remove("--calibrate")
    backend = None
    if "--backend" in args:
        i = args.index("--backend")
        backend = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]

    r = _build(backend)
    mode = "hybrid (BM25 + vector + rerank)" if r.is_hybrid else "BM25 thuần"
    if backend == "hybrid" and not r.is_hybrid:
        mode += "  ⚠ yêu cầu hybrid nhưng đã LÙI VỀ bm25 — xem cảnh báo trong log"
    print(f"corpus: {len(r.chunks)} mục · retrieval: {mode}\n")

    if calibrate:
        return _calibrate(r)

    if args:
        q = " ".join(args)
        hits = r.search(q, top_k=3)
        print(f"{q!r}")
        for c, s in hits or []:
            print(f"   {s:.5f}  {c.title}")
        if not hits:
            print("   (từ chối — không đủ tự tin)")
            for rank, (idx, score, strong) in enumerate(r._candidates(q)[:3]):
                print(f"   [bị chặn] hạng {rank + 1} {score:.5f} "
                      f"{'mạnh ' if strong else ''}{r.chunks[idx].title}")
        return 0

    bad = 0
    hit1 = hit3 = blocked = 0
    print("── phải trả lời được " + "─" * 45)
    for q, want in MUST_ANSWER:
        hits = r.search(q, top_k=1)
        got = hits[0][0].title if hits else None
        ok = got is not None and want.lower() in got.lower()
        bad += not ok
        rank = _rank_of(r, q, want)
        hit1 += rank == 0
        hit3 += rank is not None and rank < 3
        # Tìm thấy đúng ở hạng 1 nhưng search() không trả -> chốt _confident chặn oan.
        blocked += rank == 0 and not ok
        print(f"  {'ok  ' if ok else 'SAI '} {q!r}\n       -> {got or '(từ chối)'}"
              + ("" if ok else f"\n       mong đợi tiêu đề chứa {want!r}"
                               f" (mục đúng đang ở hạng {rank + 1 if rank is not None else '—'})"))

    print("\n── phải từ chối " + "─" * 50)
    for q in MUST_REJECT:
        hits = r.search(q, top_k=1)
        ok = not hits
        bad += not ok
        print(f"  {'ok  ' if ok else 'SAI '} {q!r}"
              + ("" if ok else f"\n       -> trót trả lời bằng {hits[0][0].title!r}"))

    n = len(MUST_ANSWER)
    total = n + len(MUST_REJECT)
    print(f"\n{total - bad}/{total} đúng." + ("" if not bad else f"  {bad} câu cần chỉnh."))
    print(f"  recall@1     {hit1}/{n} ({hit1 / n:.0%})")
    print(f"  recall@3     {hit3}/{n} ({hit3 / n:.0%})   "
          f"← chênh {(hit3 - hit1) / n:.0%} là phần rerank có thể cứu")
    print(f"  từ chối oan  {blocked}/{n}   ← đúng hạng 1 nhưng _confident chặn")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
