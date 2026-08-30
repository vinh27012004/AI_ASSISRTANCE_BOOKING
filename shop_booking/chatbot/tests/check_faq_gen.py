"""Soi chất lượng bước SINH (chữ G) — chạy sau khi sửa prompt hoặc đổi model.

    python tests/check_faq_gen.py            # chạy hết bộ câu hỏi mẫu
    python tests/check_faq_gen.py "câu hỏi"  # một câu, in cả bản gốc lẫn bản sinh

CẦN router thật (LLM_BASE_URL trong .env). Không có thì thoát sớm — khác `check_faq.py`
vốn chạy được offline.

Ba thứ đo được, tất định, KHÔNG dùng LLM-judge:

1. **Độ bám nguồn** — trích mọi cụm chữ số trong câu sinh và khẳng định từng cụm có trong
   chunk gốc. Bắt đúng lỗi nguy hiểm nhất của RAG: model tự chế con số. Không bắt được mọi
   kiểu bịa, nhưng bắt được kiểu tốn tiền của khách ("hủy trước 2 tiếng" khi corpus ghi 1).
   Chọn cách này thay vì LLM-judge vì nó rẻ, chạy lại cho kết quả y hệt, và không cần thêm
   một model nữa để tin tưởng.
2. **Tỉ lệ lùi nguyên văn** theo từng hàng rào — biết prompt hay timeout đang có vấn đề.
3. **Độ trễ p50/p95** của riêng lượt gọi trong `_augment`.
"""

import logging
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")
logging.disable(logging.CRITICAL)

from app import retrieval
from app.answers import faq
from app.config import load_settings
from app.llm_client import build_llm
from check_faq import MUST_ANSWER

# Cụm chữ số ≥1 ký tự. Bỏ qua số trong placeholder PII ({{phone_1}}) — chúng do masker sinh,
# không phải dữ kiện của corpus.
_NUM_RE = re.compile(r"\d+")
_PLACEHOLDER_RE = re.compile(r"\{\{[^}]*\}\}")


def _numbers(text: str) -> list[str]:
    return _NUM_RE.findall(_PLACEHOLDER_RE.sub(" ", text or ""))


def _pct(part: int, whole: int) -> str:
    return f"{part}/{whole} ({part / whole:.0%})" if whole else "—"


def main() -> int:
    settings = load_settings()
    llm = build_llm(settings)
    if llm is None:
        print("Chưa cấu hình LLM_BASE_URL/LLM_API_KEY -> bước sinh không chạy được.\n"
              "Đây là thước đo cho chữ G, cần router thật. Bỏ qua.")
        return 0
    if not settings.faq_generate:
        print("FAQ_GENERATE=0 -> bước sinh đang tắt. Bật lên rồi chạy lại.")
        return 0

    r = retrieval.build_retriever(settings)
    faq.configure(r, llm, generate=True, timeout=settings.llm_timeout_faq)
    print(f"corpus: {len(r.chunks)} mục · model: {settings.llm_model} "
          f"· hạn chờ {settings.llm_timeout_faq}s\n")

    queries = [(" ".join(sys.argv[1:]), None)] if len(sys.argv) > 1 else MUST_ANSWER
    verbose = len(sys.argv) > 1

    reasons: dict[str, int] = {}
    times: list[float] = []
    ungrounded = []
    generated = 0

    for q, _want in queries:
        hits = r.search(q, top_k=1)
        if not hits:
            reasons["truy xuất từ chối"] = reasons.get("truy xuất từ chối", 0) + 1
            print(f"  --   {q!r}\n       (retrieval từ chối — xem check_faq.py)")
            continue

        chunk = hits[0][0]
        source = chunk.answer_text
        t0 = time.perf_counter()
        text, why = faq._augment(source, q)
        times.append(time.perf_counter() - t0)
        reasons[why] = reasons.get(why, 0) + 1

        if text is None:
            print(f"  gốc  {q!r}\n       lùi nguyên văn ({why})")
            continue

        generated += 1
        # Mọi con số trong câu sinh phải có trong chunk gốc.
        extra = [n for n in _numbers(text) if n not in _numbers(source)]
        flag = "SỐ LẠ" if extra else "ok  "
        if extra:
            ungrounded.append((q, extra, text))
        print(f"  {flag} {q!r}\n       {text}")
        if verbose:
            print(f"       --- gốc: {source}")

    n = len(times)
    print("\n" + "─" * 66)
    print(f"  sinh được    {_pct(generated, n)}")
    print(f"  bám nguồn    {_pct(generated - len(ungrounded), generated)}"
          "   ← mọi con số trong câu sinh đều có trong chunk")
    if times:
        s = sorted(times)
        p95 = s[min(int(len(s) * 0.95), len(s) - 1)]
        print(f"  độ trễ       p50 {s[len(s) // 2]:.2f}s · p95 {p95:.2f}s")
    print("  lùi nguyên văn theo lý do:")
    for why, cnt in sorted(reasons.items(), key=lambda x: -x[1]):
        if why != "ok":
            print(f"      {why:<18} {cnt}")

    if ungrounded:
        print("\n  ⚠ CÂU CÓ SỐ KHÔNG CÓ TRONG NGUỒN — sửa prompt hoặc hạ temperature:")
        for q, extra, text in ungrounded:
            print(f"      {q!r}\n        số lạ {extra}\n        {text}")
    return 1 if ungrounded else 0


if __name__ == "__main__":
    sys.exit(main())
