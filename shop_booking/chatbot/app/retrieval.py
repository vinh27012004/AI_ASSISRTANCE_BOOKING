"""Retrieval cho làn QUERY — BM25 thuần stdlib (RAG, GĐ2.1).

Vì sao TỰ VIẾT chứ không kéo thư viện: corpus FAQ cỡ vài chục–vài trăm đoạn. Ở cỡ đó
vector DB (Chroma/Qdrant/pgvector) là thừa. Giữ được nguyên tắc "runnable offline" của
service: không cần mạng, không cần key, test không phải mock gì.

Vì sao BỎ nhánh vector (26/8): đo trên 8 câu khách nói tự nhiên thì BM25 trả lời được 3.
Với 5 câu còn lại, ép thẳng chunk ĐÚNG lên hạng 1 — tức giả lập một nhánh vector hoàn hảo
— thì CẢ 5 vẫn bị `Retriever._confident` chặn, vì chốt đó thuần từ vựng và có quyền phủ
quyết cuối cùng. Nhánh vector chỉ xếp lại thứ hạng, không có tiếng nói ở chốt ấy, nên nó
cứu được 0/8. Thêm một dòng '> ' vào data/faq.md thì cứu được 5/5.

=> Muốn thêm semantic recall sau này phải sửa CẢ HAI: dựng nhánh mới VÀ nới `_confident`
cho ứng viên đến từ nhánh đó. Bật mỗi nhánh mới là tiêu tài nguyên vô ích.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Tham số BM25 chuẩn (Robertson/Sparck Jones). Không có lý do chỉnh khi corpus còn nhỏ.
_K1 = 1.5
_B = 0.75

_TOKEN_RE = re.compile(r"[0-9a-zà-ỹ]+", re.IGNORECASE)

# Hư từ tiếng Việt + từ để hỏi. Bỏ đi để 'cửa hàng có mở không' không khớp mọi đoạn chỉ vì
# chung chữ 'có/không'. Cố ý KHÔNG bỏ từ nghiệp vụ ('hủy', 'sửa', 'nhóm', 'add-on').
_STOPWORDS = {
    "là", "la", "và", "va", "của", "cua", "cho", "với", "voi", "thì", "thi", "mà", "ma",
    "ở", "o", "tại", "tai", "trong", "ra", "vào", "vao", "được", "duoc", "không", "khong",
    "có", "co", "bị", "bi", "các", "cac", "những", "nhung", "một", "mot", "này", "nay",
    "đó", "do", "kia", "ạ", "à", "ừ", "nhé", "nhe", "ni", "em", "anh", "chị", "tôi", "toi",
    "mình", "minh", "bên", "ben", "bạn", "ban", "sẽ", "se", "đã", "da", "đang", "dang",
    "cũng", "cung", "rất", "rat", "quá", "qua", "lắm", "lam", "hơn", "hon", "nữa", "nua",
    "gì", "gi", "sao", "vậy", "vay", "thế", "the", "ạ̀", "hả", "ha", "ai", "khi", "nếu",
    "neu", "để", "de", "phải", "phai", "cần", "can", "muốn", "muon", "xin", "hỏi", "hoi",
    "nào", "nao", "đâu", "dau", "làm", "lam", "luôn", "luon",
    # Cụm để HỎI, không mang chủ đề: giữ lại thì mọi câu "… bao nhiêu?" khớp lẫn nhau.
    "bao", "nhiêu", "nhieu",
}


def content_tokens(text: str) -> list[str]:
    """Âm tiết mang nghĩa — đã bỏ hư từ và từ để hỏi."""
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOPWORDS]


def bigrams(tokens: list[str]) -> list[str]:
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def tokenize(text: str) -> list[str]:
    """Unigram + bigram, CẢ HAI dựng từ âm tiết nội dung.

    Bigram là bắt buộc với tiếng Việt vì một âm tiết KHÔNG phải một từ: khớp âm tiết đơn
    cho ra 'thời tiết' ~ 'thời lượng' (chung mỗi chữ 'thời') — đúng lỗi gặp lúc thử thật.
    Cặp âm tiết phân biệt được hai cụm đó mà không cần thư viện tách từ.

    Bigram dựng SAU khi lọc hư từ, không dựng từ chuỗi gốc: giữ hư từ lại thì 'thế_nào'
    thành một token nội dung, và mọi câu hỏi kết thúc bằng 'thế nào' đều khớp lẫn nhau."""
    uni = content_tokens(text)
    return uni + bigrams(uni)


@dataclass
class Chunk:
    """Một mục FAQ. `title` là dòng '## ...' trong file corpus."""
    id: str
    title: str
    text: str
    # Cách hỏi khác (dòng '> ' trong corpus): CHỈ để tìm kiếm, không đọc cho khách. Đây là
    # núm chỉnh chất lượng dành cho người viết FAQ — thấy câu nào bot không nhận ra thì
    # thêm một dòng '> ', không phải sửa code.
    aliases: list[str] = field(default_factory=list)
    tokens: list[str] = field(default_factory=list)
    # Token của RIÊNG phía câu hỏi (tiêu đề + alias), không có thân bài. Xem _confident:
    # thân bài là câu TRẢ LỜI, nó sinh ra bigram lạc đề ('cửa_hàng' trong "gọi cửa hàng để
    # được hỗ trợ") khiến câu hỏi về địa điểm khớp nhầm vào mục nói chuyện sửa/hủy. Thân
    # bài vẫn được dùng để XẾP HẠNG, chỉ không được quyền quyết định độ tự tin.
    q_tokens: list[str] = field(default_factory=list)

    @property
    def answer_text(self) -> str:
        """Câu trả lời đã gộp về MỘT dòng. Corpus xuống dòng cho dễ đọc/dễ review trong
        git, nhưng widget hiển thị nguyên văn nên để nguyên là khách thấy câu bị ngắt
        giữa chừng."""
        return " ".join(self.text.split())


# --------------------------------------------------------------------------- #
#  Corpus                                                                      #
# --------------------------------------------------------------------------- #
def load_corpus(path: str) -> list[Chunk]:
    """Đọc file markdown, cắt theo heading '## '. Mỗi mục = một chunk.

    Cắt theo heading chứ không cắt cửa sổ trượt N ký tự: FAQ vốn đã được người viết chia
    theo chủ đề, tôn trọng ranh giới đó thì mỗi chunk là một câu trả lời TRỌN VẸN — trả
    thẳng cho khách được, không cần LLM ghép lại (xem app/answers/faq.py)."""
    if not path or not os.path.exists(path):
        logger.warning("retrieval: không thấy corpus %r -> FAQ tắt", path)
        return []
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    chunks: list[Chunk] = []
    title, buf = None, []

    def _flush():
        if not (title and buf):
            return
        # Dòng '> ' là cách hỏi khác — tách khỏi phần đọc cho khách.
        aliases = [ln[1:].strip() for ln in buf if ln.lstrip().startswith(">")]
        body = "\n".join(ln for ln in buf if not ln.lstrip().startswith(">")).strip()
        if not body:
            return
        chunks.append(Chunk(
            id=f"faq-{len(chunks) + 1}",
            title=title,
            text=body,
            aliases=aliases,
            # Tiêu đề và cách-hỏi-khác được đếm HAI LẦN: chúng là câu HỎI, khớp với lời
            # khách sát hơn phần thân (vốn là câu TRẢ LỜI, dùng từ ngữ khác hẳn).
            tokens=tokenize(" ".join([title, title, *aliases, *aliases, body])),
            q_tokens=tokenize(" ".join([title, *aliases])),
        ))

    for line in raw.splitlines():
        if line.startswith("## "):
            _flush()
            title, buf = line[3:].strip(), []
        elif title is not None:
            buf.append(line)
    _flush()

    logger.info("retrieval: nạp %d mục FAQ từ %s", len(chunks), path)
    return chunks


# --------------------------------------------------------------------------- #
#  BM25                                                                        #
# --------------------------------------------------------------------------- #
class BM25Index:
    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.n = len(chunks)
        self.tf: list[dict[str, int]] = []
        self.len: list[int] = []
        df: dict[str, int] = {}
        for c in chunks:
            counts: dict[str, int] = {}
            for t in c.tokens:
                counts[t] = counts.get(t, 0) + 1
            self.tf.append(counts)
            self.len.append(len(c.tokens))
            for t in counts:
                df[t] = df.get(t, 0) + 1
        self.avgdl = (sum(self.len) / self.n) if self.n else 0.0
        # idf tính sẵn — corpus tĩnh nên không việc gì tính lại mỗi truy vấn.
        self.idf = {t: math.log(1 + (self.n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str) -> list[tuple[int, float]]:
        """Trả [(chỉ số chunk, điểm)] giảm dần, bỏ điểm 0."""
        q = tokenize(query)
        if not q or not self.n:
            return []
        out = []
        for i in range(self.n):
            tf, dl = self.tf[i], self.len[i]
            s = 0.0
            for t in q:
                f = tf.get(t)
                if not f:
                    continue
                s += self.idf.get(t, 0.0) * (f * (_K1 + 1)) / (
                    f + _K1 * (1 - _B + _B * dl / (self.avgdl or 1))
                )
            if s > 0:
                out.append((i, s))
        out.sort(key=lambda x: -x[1])
        return out


# --------------------------------------------------------------------------- #
#  Retriever                                                                   #
# --------------------------------------------------------------------------- #
class Retriever:
    """BM25 + chốt độ tự tin. Xem docstring module vì sao không còn nhánh vector."""

    # Ngưỡng chặn "không biết". BM25 trả điểm dương cho bất kỳ chunk nào chung MỘT token,
    # nên không chặn thì câu lạc đề hoàn toàn vẫn moi ra một mục FAQ và bot trả lời tự tin
    # nhưng sai. Đo bằng ĐỘ PHỦ token truy vấn thay vì ngưỡng điểm tuyệt đối — điểm BM25
    # phụ thuộc kích thước corpus nên ngưỡng tuyệt đối phải chỉnh lại mỗi lần thêm FAQ,
    # còn độ phủ thì không.
    _MIN_COVERAGE = 0.34

    def __init__(self, chunks: list[Chunk]):
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)

    # -- search ------------------------------------------------------------- #
    def _confident(self, idx: int, query: str) -> bool:
        """Hai điều kiện, phải đạt CẢ HAI.

        1. Độ phủ âm tiết nội dung ≥ _MIN_COVERAGE.
        2. Trùng ít nhất MỘT bigram VỚI PHÍA CÂU HỎI của mục (tiêu đề + alias), khi câu
           hỏi dài đủ để có bigram. Đây là chốt thật sự với tiếng Việt: trùng một âm tiết
           lẻ ('thời') là ngẫu nhiên, trùng một cặp ('mã_đặt', 'hủy_lịch') thì gần như
           chắc chắn cùng chủ đề.

        Điều kiện 1 tính trên TOÀN mục (kể cả thân bài) để không bỏ sót; điều kiện 2 chỉ
        tính trên phía câu hỏi để không nhận bừa. Recall ở chốt một, precision ở chốt hai."""
        q_uni = set(content_tokens(query))
        if not q_uni:
            return False
        chunk = self.chunks[idx]
        have_uni = {t for t in chunk.tokens if "_" not in t}
        if len(q_uni & have_uni) / len(q_uni) < self._MIN_COVERAGE:
            return False
        q_bi = set(bigrams(content_tokens(query)))
        if not q_bi:                       # câu 1 âm tiết -> chỉ còn điều kiện độ phủ
            return True
        return bool(q_bi & {t for t in chunk.q_tokens if "_" in t})

    def search(self, query: str, top_k: int = 1) -> list[tuple[Chunk, float]]:
        """Trả [(chunk, điểm BM25)]. Rỗng = không đủ tự tin để trả lời.

        Cắt top_k TRƯỚC rồi mới lọc theo `_confident`, không phải ngược lại: mục hạng 1
        trượt chốt nghĩa là câu hỏi này không thuộc chủ đề nào cả, tụt xuống lấy hạng 2
        chỉ là đoán bừa xa hơn."""
        if not self.chunks:
            return []

        out = []
        for idx, score in self.bm25.search(query)[:top_k]:
            if not self._confident(idx, query):
                logger.debug("retrieval: bỏ %r (không đủ tự tin cho %r)",
                             self.chunks[idx].title, query)
                continue
            out.append((self.chunks[idx], score))
        return out


def build_retriever(settings) -> Retriever:
    """Dựng từ Settings. Corpus rỗng/không thấy file -> Retriever không chunk, FAQ tắt."""
    return Retriever(load_corpus(settings.faq_corpus_path))
