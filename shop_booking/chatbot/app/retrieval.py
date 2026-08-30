"""Retrieval cho làn QUERY — BM25 thuần stdlib. Đây là chữ R của RAG; chữ G (diễn đạt lại
theo chunk) nằm ở `app/answers/faq.py::_augment`.

HAI BACKEND, chọn bằng RAG_BACKEND trong .env:

- `bm25` (mặc định) — BM25 thuần stdlib. Không mạng, không key, không cài gì. Đây là đường
  mà toàn bộ test chạy, và là đường lùi về khi mọi thứ khác hỏng.
- `hybrid` — BM25 + vector (Chroma, bi-encoder tiếng Việt) hợp nhất bằng RRF, rồi xếp lại
  bằng cross-encoder PhoRanker. Cần ~2-3GB thư viện, nạp model ~10-30s lúc boot.

LỊCH SỬ QUAN TRỌNG, đừng lặp lại: nhánh vector từng bị gỡ ngày 26/8 vì đo ra cứu 0/8 câu.
Nguyên nhân KHÔNG phải vector kém, mà là `Retriever._confident` — chốt thuần từ vựng có
quyền phủ quyết cuối — chặn sạch những câu mà ngữ nghĩa vừa cứu được. Lần này nhánh vector
đi kèm `_confident(strong=...)`: ứng viên được cross-encoder chấm trên ngưỡng thì được miễn
điều kiện bigram. Dựng nhánh mới mà quên nới chốt = lặp lại đúng thất bại cũ.

Phép đo cũ còn một điều kiện biên nữa: nó làm trên corpus 21 mục, nơi BM25 gần như luôn
xếp đúng hạng 1. Ở cỡ vài trăm mục thì ràng buộc chặt nhất chuyển sang chỗ khác, nên phải
đo lại bằng `tests/check_faq.py --backend hybrid` chứ không suy từ kết luận cũ.
"""

from __future__ import annotations

import hashlib
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
    # File corpus chứa mục này. Với một file thì thừa; với vài chục file thì đây là thứ duy
    # nhất cho biết câu trả lời sai đến từ đâu mà đi sửa.
    source: str = ""
    # Text đã tách từ cho nhánh vector/rerank (PhoBERT bắt buộc input tách từ). Để rỗng ở
    # backend BM25 — tách từ cần pyvi, mà lõi không được phụ thuộc gói ngoài.
    seg_text: str = ""

    @property
    def answer_text(self) -> str:
        """Câu trả lời đã gộp về MỘT dòng. Corpus xuống dòng cho dễ đọc/dễ review trong
        git, nhưng widget hiển thị nguyên văn nên để nguyên là khách thấy câu bị ngắt
        giữa chừng."""
        return " ".join(self.text.split())


# --------------------------------------------------------------------------- #
#  Corpus                                                                      #
# --------------------------------------------------------------------------- #
def _parse_markdown(raw: str, source: str) -> list[Chunk]:
    """Cắt một file markdown theo heading '## '. Mỗi mục = một chunk.

    Cắt theo heading chứ không cắt cửa sổ trượt N ký tự: FAQ vốn đã được người viết chia
    theo chủ đề, tôn trọng ranh giới đó thì mỗi chunk là một câu trả lời TRỌN VẸN — đủ
    dùng thẳng khi bước sinh không chạy được (xem app/answers/faq.py)."""
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
            # Tên file vào id: log chỉ ra 'dich-vu-3' là biết mở file nào, không phải dò.
            id=f"{source}-{len(chunks) + 1}",
            title=title,
            text=body,
            aliases=aliases,
            # Tiêu đề và cách-hỏi-khác được đếm HAI LẦN: chúng là câu HỎI, khớp với lời
            # khách sát hơn phần thân (vốn là câu TRẢ LỜI, dùng từ ngữ khác hẳn).
            tokens=tokenize(" ".join([title, title, *aliases, *aliases, body])),
            q_tokens=tokenize(" ".join([title, *aliases])),
            source=source,
        ))

    for line in raw.splitlines():
        if line.startswith("## "):
            _flush()
            title, buf = line[3:].strip(), []
        elif title is not None:
            buf.append(line)
    _flush()
    return chunks


def load_corpus(path: str) -> list[Chunk]:
    """Nạp corpus từ MỘT file .md hoặc từ cả một THƯ MỤC chứa nhiều .md.

    Cho phép thư mục vì kho vài trăm mục trong một file thì không review nổi qua git. Sắp
    file theo tên để thứ tự chunk (và do đó id) ổn định giữa các lần chạy — không thì mỗi
    lần khởi động lại sinh id khác, cache vector đánh theo id thành vô dụng."""
    if not path or not os.path.exists(path):
        logger.warning("retrieval: không thấy corpus %r -> FAQ tắt", path)
        return []

    if os.path.isdir(path):
        files = sorted(f for f in os.listdir(path) if f.endswith(".md"))
        paths = [os.path.join(path, f) for f in files]
    else:
        paths = [path]

    chunks: list[Chunk] = []
    for p in paths:
        source = os.path.splitext(os.path.basename(p))[0]
        with open(p, encoding="utf-8") as f:
            chunks.extend(_parse_markdown(f.read(), source))

    if len(paths) > 1:
        logger.info("retrieval: nạp %d mục FAQ từ %d file trong %s",
                    len(chunks), len(paths), path)
    else:
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
#  Hợp nhất hai bảng xếp hạng — RRF                                            #
# --------------------------------------------------------------------------- #
_RRF_K = 60


def rrf_fuse(rankings: list[list[int]], k: int = _RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: điểm = tổng 1/(k + hạng). Chỉ nhìn THỨ HẠNG nên không phải
    chuẩn hóa giữa thang BM25 (không chặn trên) và thang cosine (0..1)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, idx in enumerate(ranking):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])


# --------------------------------------------------------------------------- #
#  Tách từ tiếng Việt — bắt buộc cho họ PhoBERT                                #
# --------------------------------------------------------------------------- #
def segment(text: str) -> str:
    """Tách từ bằng pyvi. PhoBERT (cả embedding lẫn PhoRanker) được huấn luyện trên text ĐÃ
    TÁCH TỪ: đưa text thô vào thì model vẫn chạy, KHÔNG báo lỗi, chỉ cho kết quả kém hẳn —
    rồi ta lại tưởng model dở. Đây là cái bẫy im lặng nhất của cả nhánh này.

    Công cụ chuẩn của PhoBERT là py_vncorenlp nhưng nó là jar Java, cần cài JDK. pyvi thuần
    Python, làm đúng việc cần. Không có pyvi -> trả text thô (nhánh vector sẽ kém, nhưng thà
    thế còn hơn sập)."""
    try:
        from pyvi import ViTokenizer
    except ImportError:
        return text
    return ViTokenizer.tokenize(text)


# --------------------------------------------------------------------------- #
#  Nhánh vector — Chroma + bi-encoder tiếng Việt                               #
# --------------------------------------------------------------------------- #
class VectorIndex:
    """Chỉ mục ngữ nghĩa. Mọi import nặng nằm TRONG hàm: lõi service phải chạy được trên
    máy chưa cài torch/chromadb (đặc tính 'runnable offline' — xem requirements.txt)."""

    def __init__(self, chunks: list[Chunk], model_name: str, store_path: str):
        from chromadb import PersistentClient
        from sentence_transformers import SentenceTransformer

        self.chunks = chunks
        self.model = SentenceTransformer(model_name)
        # Tách từ TRƯỚC và LUÔN LUÔN, không phải chỉ khi dựng lại index. Trước đây việc này
        # nằm trong _ensure_collection nên lần chạy nào hash khớp (tức gần như mọi lần) thì
        # seg_text rỗng, và Reranker rơi vào nhánh dự phòng chấm câu hỏi với MỖI THÂN BÀI —
        # mất tiêu đề + alias, tức mất đúng phần mang cách khách hỏi. Đo được: rerank khi đó
        # đẩy đáp án đúng từ hạng 1 xuống hạng 3.
        for c in chunks:
            c.seg_text = segment(" ".join([c.title, *c.aliases, c.text]))
        self._client = PersistentClient(path=store_path)
        self._collection = self._ensure_collection(chunks)

    @staticmethod
    def _corpus_hash(chunks: list[Chunk]) -> str:
        """Băm nội dung corpus. Không có cái này thì mỗi lần khởi động Flask lại ngồi embed
        lại vài trăm chunk — nhân với thời gian nạp model là vòng lặp dev không chịu nổi."""
        h = hashlib.sha256()
        for c in chunks:
            h.update(c.id.encode("utf-8"))
            h.update(c.text.encode("utf-8"))
            h.update("|".join(c.aliases).encode("utf-8"))
        return h.hexdigest()[:16]

    def _ensure_collection(self, chunks: list[Chunk]):
        want = self._corpus_hash(chunks)
        existing = self._client.get_or_create_collection("faq")
        if (existing.metadata or {}).get("corpus_hash") == want and existing.count():
            logger.info("retrieval: dùng lại vector index (hash=%s, %d mục)",
                        want, existing.count())
            return existing

        logger.info("retrieval: corpus đổi -> embed lại %d mục", len(chunks))
        self._client.delete_collection("faq")
        col = self._client.create_collection("faq", metadata={"corpus_hash": want})
        if not chunks:
            return col

        # Embed cùng nguyên liệu mà BM25 dùng để xếp hạng: tiêu đề + cách hỏi khác + thân
        # bài (đã tách từ ở __init__). Tiêu đề/alias là câu HỎI nên chúng kéo vector về phía
        # lời khách nói.
        vecs = self.model.encode([c.seg_text for c in chunks],
                                 show_progress_bar=False).tolist()
        col.add(ids=[c.id for c in chunks], embeddings=vecs,
                metadatas=[{"pos": i} for i in range(len(chunks))])
        return col

    def search(self, query: str, top_k: int) -> list[int]:
        """Trả danh sách CHỈ SỐ chunk theo thứ hạng ngữ nghĩa."""
        if not self.chunks:
            return []
        vec = self.model.encode([segment(query)], show_progress_bar=False).tolist()
        res = self._collection.query(query_embeddings=vec, n_results=min(top_k, len(self.chunks)))
        metas = (res.get("metadatas") or [[]])[0]
        return [m["pos"] for m in metas if m and "pos" in m]


# --------------------------------------------------------------------------- #
#  Rerank — cross-encoder PhoRanker                                            #
# --------------------------------------------------------------------------- #
class Reranker:
    """Cross-encoder: nhận CẶP (câu hỏi, đoạn) cùng lúc nên chấm chính xác hơn bi-encoder,
    đổi lại không precompute được — phải chạy k lần cho mỗi câu hỏi. Vì vậy nó chỉ xếp lại
    top-k của bước truy hồi, không bao giờ quét cả corpus."""

    def __init__(self, model_name: str):
        from sentence_transformers import CrossEncoder
        self.model = CrossEncoder(model_name)

    @staticmethod
    def _to_prob(scores) -> list[float]:
        """Đưa điểm về thang 0..1 để ngưỡng có nghĩa cố định. Có model đã bọc sigmoid sẵn,
        có model trả logit thô — bọc hai lần thì thang méo, nên chỉ bọc khi thấy điểm nằm
        ngoài [0,1]."""
        vals = [float(s) for s in scores]
        if all(0.0 <= v <= 1.0 for v in vals):
            return vals
        return [1.0 / (1.0 + math.exp(-v)) for v in vals]

    def rank(self, query: str, chunks: list[Chunk], idxs: list[int]) -> list[tuple[int, float]]:
        if not idxs:
            return []
        q = segment(query)
        pairs = [(q, chunks[i].seg_text or segment(chunks[i].answer_text)) for i in idxs]
        scored = list(zip(idxs, self._to_prob(self.model.predict(pairs))))
        scored.sort(key=lambda x: -x[1])
        return scored


# --------------------------------------------------------------------------- #
#  Retriever                                                                   #
# --------------------------------------------------------------------------- #
class Retriever:
    """BM25 (luôn có) + tuỳ chọn nhánh vector & rerank. Xem docstring module."""

    # Ngưỡng chặn "không biết". BM25 trả điểm dương cho bất kỳ chunk nào chung MỘT token,
    # nên không chặn thì câu lạc đề hoàn toàn vẫn moi ra một mục FAQ và bot trả lời tự tin
    # nhưng sai. Đo bằng ĐỘ PHỦ token truy vấn thay vì ngưỡng điểm tuyệt đối — điểm BM25
    # phụ thuộc kích thước corpus nên ngưỡng tuyệt đối phải chỉnh lại mỗi lần thêm FAQ,
    # còn độ phủ thì không.
    _MIN_COVERAGE = 0.34

    # Điểm PhoRanker (0..1) từ mức này trở lên thì ứng viên được MIỄN điều kiện bigram.
    # PHẢI hiệu chỉnh bằng số liệu, không đặt cảm tính:
    #     python tests/check_faq.py --backend hybrid --calibrate
    # in phân bố điểm cho câu đúng và câu phải-từ-chối; chọn ngưỡng CAO NHẤT mà MUST_REJECT
    # vẫn 10/10. Đặt thấp quá là mở cửa cho trả lời sai chủ đề — thứ tệ hơn mọi cải thiện
    # recall cộng lại.
    _RERANK_STRONG = 0.5

    def __init__(self, chunks: list[Chunk], vector=None, reranker=None,
                 retrieve_top_k: int = 10):
        self.chunks = chunks
        self.bm25 = BM25Index(chunks)
        self.vector = vector
        self.reranker = reranker
        self.retrieve_top_k = retrieve_top_k

    @property
    def is_hybrid(self) -> bool:
        return self.vector is not None

    # -- search ------------------------------------------------------------- #
    def _confident(self, idx: int, query: str, strong: bool = False) -> bool:
        """Hai điều kiện, phải đạt CẢ HAI (trừ khi `strong`).

        1. Độ phủ âm tiết nội dung ≥ _MIN_COVERAGE.
        2. Trùng ít nhất MỘT bigram VỚI PHÍA CÂU HỎI của mục (tiêu đề + alias), khi câu
           hỏi dài đủ để có bigram. Đây là chốt thật sự với tiếng Việt: trùng một âm tiết
           lẻ ('thời') là ngẫu nhiên, trùng một cặp ('mã_đặt', 'hủy_lịch') thì gần như
           chắc chắn cùng chủ đề.

        Điều kiện 1 tính trên TOÀN mục (kể cả thân bài) để không bỏ sót; điều kiện 2 chỉ
        tính trên phía câu hỏi để không nhận bừa. Recall ở chốt một, precision ở chốt hai.

        `strong=True` (cross-encoder chấm trên _RERANK_STRONG) BỎ điều kiện 2. Đây chính là
        chỗ mà lần thử nhánh vector trước đây chết: điều kiện 2 thuần từ vựng và có quyền
        phủ quyết cuối, nên nó chặn đúng những câu mà ngữ nghĩa vừa cứu được — hỏi 'gói kéo
        dài bao lâu' không trùng bigram nào với 'Thời lượng dịch vụ tính thế nào'. Điều kiện
        1 vẫn giữ: cross-encoder cũng sai được, và độ phủ là lưới cuối chặn câu lạc đề."""
        q_uni = set(content_tokens(query))
        if not q_uni:
            return False
        chunk = self.chunks[idx]
        have_uni = {t for t in chunk.tokens if "_" not in t}
        if len(q_uni & have_uni) / len(q_uni) < self._MIN_COVERAGE:
            return False
        if strong:
            return True
        q_bi = set(bigrams(content_tokens(query)))
        if not q_bi:                       # câu 1 âm tiết -> chỉ còn điều kiện độ phủ
            return True
        return bool(q_bi & {t for t in chunk.q_tokens if "_" in t})

    def _candidates(self, query: str) -> list[tuple[int, float, bool]]:
        """Trả [(chỉ số chunk, điểm, đã-được-rerank-chấm-mạnh)] theo thứ hạng cuối."""
        bm25_hits = self.bm25.search(query)
        if not self.is_hybrid:
            return [(i, s, False) for i, s in bm25_hits]

        k = self.retrieve_top_k
        rankings = [[i for i, _ in bm25_hits[:k]]]
        try:
            rankings.append(self.vector.search(query, k))
        except Exception as e:             # model/Chroma hỏng giữa chừng -> còn BM25
            logger.warning("retrieval: nhánh vector lỗi (%s) -> chỉ dùng BM25", e)

        fused = rrf_fuse(rankings)[:k]
        if self.reranker is None:
            return [(i, s, False) for i, s in fused]

        try:
            scored = self.reranker.rank(query, self.chunks, [i for i, _ in fused])
        except Exception as e:
            logger.warning("retrieval: rerank lỗi (%s) -> giữ thứ hạng RRF", e)
            return [(i, s, False) for i, s in fused]
        return [(i, s, s >= self._RERANK_STRONG) for i, s in scored]

    def search(self, query: str, top_k: int = 1) -> list[tuple[Chunk, float]]:
        """Trả [(chunk, điểm)]. Rỗng = không đủ tự tin để trả lời.

        Cắt top_k TRƯỚC rồi mới lọc theo `_confident`, không phải ngược lại: mục hạng 1
        trượt chốt nghĩa là câu hỏi này không thuộc chủ đề nào cả, tụt xuống lấy hạng 2
        chỉ là đoán bừa xa hơn."""
        if not self.chunks:
            return []

        out = []
        for idx, score, strong in self._candidates(query)[:top_k]:
            if not self._confident(idx, query, strong=strong):
                logger.debug("retrieval: bỏ %r (không đủ tự tin cho %r)",
                             self.chunks[idx].title, query)
                continue
            out.append((self.chunks[idx], score))
        return out


def build_retriever(settings) -> Retriever:
    """Dựng từ Settings. Corpus rỗng/không thấy file -> Retriever không chunk, FAQ tắt.

    RAG_BACKEND=hybrid mà thiếu gói (chromadb/sentence-transformers/pyvi) hoặc tải model
    hỏng -> ghi cảnh báo rồi lùi về BM25. Service không được sập chỉ vì một nhánh tuỳ chọn."""
    chunks = load_corpus(settings.faq_corpus_path)
    if not chunks or not getattr(settings, "use_hybrid", False):
        return Retriever(chunks)

    try:
        vector = VectorIndex(chunks, settings.embedding_model, settings.vector_store_path)
        reranker = Reranker(settings.rerank_model)
    except Exception as e:
        logger.warning("retrieval: không dựng được nhánh hybrid (%s) -> BM25 thuần", e)
        return Retriever(chunks)

    logger.info("retrieval: hybrid BM25 + vector(%s) + rerank(%s)",
                settings.embedding_model, settings.rerank_model)
    return Retriever(chunks, vector=vector, reranker=reranker,
                     retrieve_top_k=settings.retrieve_top_k)
