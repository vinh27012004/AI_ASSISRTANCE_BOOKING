"""Retrieval cho làn QUERY — hybrid BM25 + vector, trộn bằng RRF (RAG, GĐ2.1).

Vì sao TỰ VIẾT chứ không kéo thư viện: corpus FAQ cỡ vài chục–vài trăm đoạn. Ở cỡ đó
vector DB (Chroma/Qdrant/pgvector) là thừa — quét tuyến tính vài trăm vector hết vài chục
micro giây. Giữ được nguyên tắc "runnable offline" của service: BM25 chạy bằng stdlib
thuần, không cần mạng, test không cần mock gì.

Hybrid vì corpus này có CẢ hai kiểu trượt:
  - BM25 gánh thuật ngữ hiếm khớp nguyên văn ('momihogushi 30', 'NG list', tên chi nhánh)
    — chỗ embedding tiếng Việt hay nuốt mất.
  - Vector gánh câu diễn đạt khác ('hủy trước bao lâu' ↔ 'chính sách thay đổi đặt chỗ').
Thiếu EMBEDDING_BASE_URL -> tự động lùi về BM25-only (vẫn dùng được, chỉ kém phần diễn giải).

Trộn bằng RRF chứ KHÔNG cộng điểm có trọng số: điểm BM25 (không chặn trên) và cosine
(0..1) khác thang, chuẩn hóa giữa hai thang là nguồn chỉnh tay bất tận. RRF chỉ nhìn THỨ
HẠNG nên miễn nhiễm chuyện đó.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# --- Tham số BM25 chuẩn (Robertson/Sparck Jones). Không có lý do chỉnh khi corpus còn nhỏ.
_K1 = 1.5
_B = 0.75
# Hằng RRF. 60 là giá trị gốc trong bài Cormack 2009, ổn định với danh sách ngắn.
_RRF_K = 60

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
#  Vector (tùy chọn)                                                           #
# --------------------------------------------------------------------------- #
class Embedder:
    """Adapter /embeddings kiểu OpenAI — cùng khuôn với app/llm_client.py (đổi provider =
    đổi base_url + api_key). Dùng urllib để lõi không thêm phụ thuộc."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def embed(self, texts: list[str]) -> list[list[float]]:
        payload = {"model": self.model, "input": texts}
        req = urllib.request.Request(
            f"{self.base_url}/embeddings",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            obj = json.loads(resp.read().decode("utf-8"))
        # Router không đảm bảo thứ tự -> sắp lại theo `index` như spec OpenAI quy định.
        items = sorted(obj["data"], key=lambda d: d.get("index", 0))
        return [it["embedding"] for it in items]


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return (num / (na * nb)) if na and nb else 0.0


# --------------------------------------------------------------------------- #
#  RRF                                                                         #
# --------------------------------------------------------------------------- #
def rrf_fuse(rankings: list[list[int]], k: int = _RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: điểm = tổng 1/(k + hạng). Chỉ nhìn THỨ HẠNG nên không phải
    chuẩn hóa giữa thang BM25 (không chặn trên) và thang cosine (0..1)."""
    score: dict[int, float] = {}
    for ranked in rankings:
        for rank, idx in enumerate(ranked, start=1):
            score[idx] = score.get(idx, 0.0) + 1.0 / (k + rank)
    return sorted(score.items(), key=lambda x: -x[1])


# --------------------------------------------------------------------------- #
#  Retriever                                                                   #
# --------------------------------------------------------------------------- #
class Retriever:
    """Hybrid retriever. `embedder=None` -> BM25-only (mặc định khi chạy offline)."""

    # Số chunk mỗi nhánh đưa vào trộn. Lấy rộng hơn top_k để nhánh kia có cơ hội kéo lên.
    _CANDIDATES = 10
    # Ngưỡng chặn "không biết". BM25 trả điểm dương cho bất kỳ chunk nào chung MỘT token,
    # nên không chặn thì câu lạc đề hoàn toàn vẫn moi ra một mục FAQ và bot trả lời tự tin
    # nhưng sai. Đo bằng ĐỘ PHỦ token truy vấn thay vì ngưỡng điểm tuyệt đối — điểm BM25
    # phụ thuộc kích thước corpus nên ngưỡng tuyệt đối phải chỉnh lại mỗi lần thêm FAQ,
    # còn độ phủ thì không.
    _MIN_COVERAGE = 0.34

    def __init__(self, chunks: list[Chunk], embedder: "Embedder | None" = None,
                 cache_path: str = ""):
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)
        self.embedder = embedder
        self.vectors: list[list[float]] = []
        if embedder and chunks:
            self.vectors = self._load_or_build_vectors(cache_path)

    # -- vector cache ------------------------------------------------------- #
    def _corpus_fingerprint(self) -> str:
        h = hashlib.sha256()
        for c in self.chunks:
            h.update(c.title.encode("utf-8"))
            h.update(c.text.encode("utf-8"))
        h.update(self.embedder.model.encode("utf-8"))
        return h.hexdigest()[:16]

    def _load_or_build_vectors(self, cache_path: str) -> list[list[float]]:
        """Embed corpus MỘT LẦN rồi ghi cache ra đĩa, khóa theo vân tay nội dung + tên
        model. Không cache thì mỗi lần khởi động lại là một lượt gọi API tính tiền và vài
        giây chờ — sửa một dòng FAQ rồi restart là thấy ngay."""
        fp = self._corpus_fingerprint()
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    blob = json.load(f)
                if blob.get("fingerprint") == fp:
                    logger.info("retrieval: dùng vector cache %s", cache_path)
                    return blob["vectors"]
                logger.info("retrieval: corpus đổi -> embed lại")
            except (OSError, ValueError, KeyError):
                logger.warning("retrieval: cache hỏng -> embed lại")
        try:
            t0 = time.perf_counter()
            vecs = self.embedder.embed([f"{c.title}\n{c.text}" for c in self.chunks])
            logger.info("retrieval: embed %d chunk trong %.2fs", len(vecs),
                        time.perf_counter() - t0)
        except (OSError, ValueError, KeyError) as e:
            # Embedding hỏng KHÔNG được làm chết service: lùi về BM25-only.
            logger.warning("retrieval: embed corpus lỗi (%s) -> BM25-only", e)
            return []
        if cache_path:
            try:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump({"fingerprint": fp, "vectors": vecs}, f)
            except OSError as e:
                logger.warning("retrieval: không ghi được cache (%s)", e)
        return vecs

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
        """Trả [(chunk, điểm RRF)]. Rỗng = không đủ tự tin để trả lời."""
        if not self.chunks:
            return []

        lexical = self.bm25.search(query)
        rankings: list[list[int]] = [[i for i, _ in lexical[:self._CANDIDATES]]]

        if self.embedder and self.vectors:
            try:
                qv = self.embedder.embed([query])[0]
                dense = sorted(
                    ((i, _cosine(qv, v)) for i, v in enumerate(self.vectors)),
                    key=lambda x: -x[1],
                )
                rankings.append([i for i, _ in dense[:self._CANDIDATES]])
            except (OSError, ValueError, KeyError, IndexError) as e:
                # Nhánh vector hỏng -> vẫn còn BM25. Câu trả lời kém đi chứ không mất.
                logger.warning("retrieval: embed truy vấn lỗi (%s) -> chỉ dùng BM25", e)

        out = []
        for idx, score in rrf_fuse(rankings)[:top_k]:
            if not self._confident(idx, query):
                logger.debug("retrieval: bỏ %r (không đủ tự tin cho %r)",
                             self.chunks[idx].title, query)
                continue
            out.append((self.chunks[idx], score))
        return out


def build_retriever(settings) -> Retriever:
    """Dựng từ Settings. Thiếu cấu hình embedding -> BM25-only, không lỗi."""
    chunks = load_corpus(settings.faq_corpus_path)
    embedder = None
    if settings.use_embeddings and chunks:
        embedder = Embedder(settings.embedding_base_url, settings.embedding_api_key,
                            settings.embedding_model)
    return Retriever(chunks, embedder, settings.faq_vector_cache_path)
