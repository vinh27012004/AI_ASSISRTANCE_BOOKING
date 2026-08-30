"""Cấu hình đọc từ biến môi trường. Không phụ thuộc thư viện ngoài.

Nguyên tắc "runnable offline": thiếu LLM_BASE_URL -> FakeLLM; thiếu REDIS_URL -> in-memory.
Nhờ vậy lõi chạy/test được ngay không cần hạ tầng (mẹo test §9)."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _load_dotenv() -> None:
    """Nạp .env cạnh service nếu có (không cần python-dotenv). Bỏ qua nếu thiếu file."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(here, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def _data_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def _default_faq_path() -> str:
    """Ưu tiên thư mục data/faq/ nếu có (kho lớn chia nhiều file), không thì file đơn
    data/faq.md như cũ — tách file ra không phải sửa .env."""
    folder = os.path.join(_data_dir(), "faq")
    if os.path.isdir(folder):
        return folder
    return os.path.join(_data_dir(), "faq.md")


def _default_vector_path() -> str:
    return os.path.join(_data_dir(), "faq_chroma")


@dataclass(frozen=True)
class Settings:
    shop_api_base_url: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    redis_url: str
    session_ttl_seconds: int
    vault_enc_key: str
    fallback_shop_phone: str
    # Số hỗ trợ/CSKH hiển thị khi khách KHÔNG đặt online được (chặn NG A5 / handoff / nhóm
    # đông A8): mọi ca "liên hệ" gom về một đầu mối thay vì số riêng từng cửa hàng. Rỗng ->
    # dùng số cửa hàng như cũ. Có default nên constructor cũ (test) không cần truyền.
    support_phone: str = ""
    # Mức log ('DEBUG'/'INFO'/'WARNING'...) — xem app/shop_api_client.py: mọi lời gọi
    # shop_api được log ở đây để soi dữ liệu sai (BE trả sai hay chatbot xử lý sai).
    log_level: str = "INFO"
    # --- FAQ / retrieval (app/retrieval.py). Corpus rỗng -> làn FAQ tắt hẳn, phần còn
    # lại chạy y như cũ. Retrieval là BM25 thuần, không cấu hình gì thêm.
    # `faq_generate` bật bước sinh (G) của RAG: chunk tìm được đi qua LLM diễn đạt lại thay
    # vì trả nguyên văn. Chưa cấu hình router thì answers/faq.py tự tắt, không cần đụng cờ.
    # Hạn chờ để RIÊNG: bước này nằm giữa câu hỏi và câu trả lời nên hỏng là khách ngồi
    # đợi, mà lùi về nguyên văn thì có sẵn -> để ngắn như NLG.
    # --- Hạn chờ LLM, tách theo chỗ gọi. Trước đây dùng chung 20s cho cả hai: NLU chặn
    # cả lượt chat nên khách phải ngồi đợi trọn 20 giây rồi mới nhận được câu rule-based.
    # NLU đo thật 2,2–4,4s (log 26/8) -> 8s là gấp đôi đầu, mà xấu nhất giảm 2,5 lần.
    # NLG giờ chỉ còn GREETING/REPROMPT, hỏng thì có câu mẫu -> để ngắn hơn nữa.
    llm_timeout_nlu: float = 8.0
    llm_timeout_nlg: float = 6.0
    faq_corpus_path: str = ""
    faq_generate: bool = True
    llm_timeout_faq: float = 6.0
    # --- Backend truy xuất. 'bm25' = như cũ, thuần stdlib, không cần cài gì. 'hybrid' =
    # BM25 + vector (Chroma) hợp nhất bằng RRF rồi xếp lại bằng cross-encoder PhoRanker.
    # Mặc định 'bm25': gói nặng (~2-3GB) là TÙY CHỌN, máy chưa cài vẫn phải chạy được —
    # retrieval.build_retriever tự lùi về bm25 nếu import hỏng.
    rag_backend: str = "bm25"
    embedding_model: str = "bkai-foundation-models/vietnamese-bi-encoder"
    rerank_model: str = "itdainb/PhoRanker"
    # Mỗi nhánh lấy bao nhiêu trước khi hợp nhất, và còn lại bao nhiêu sau khi rerank.
    retrieve_top_k: int = 10
    final_top_k: int = 3
    vector_store_path: str = ""

    @property
    def use_hybrid(self) -> bool:
        return self.rag_backend.strip().lower() == "hybrid"

    @property
    def use_real_llm(self) -> bool:
        # Có endpoint + key -> gọi router thật; nếu không, FakeLLM để dev/test offline.
        return bool(self.llm_base_url and self.llm_api_key)

    @property
    def use_redis(self) -> bool:
        return bool(self.redis_url)


def load_settings() -> Settings:
    _load_dotenv()
    return Settings(
        shop_api_base_url=os.environ.get("SHOP_API_BASE_URL", "http://127.0.0.1:5000/api/v1").rstrip("/"),
        llm_base_url=os.environ.get("LLM_BASE_URL", "").rstrip("/"),
        llm_api_key=os.environ.get("LLM_API_KEY", ""),
        llm_model=os.environ.get("LLM_MODEL", "gpt-4o-mini"),
        redis_url=os.environ.get("REDIS_URL", ""),
        session_ttl_seconds=int(os.environ.get("SESSION_TTL_SECONDS", "1800")),
        vault_enc_key=os.environ.get("VAULT_ENC_KEY", ""),
        fallback_shop_phone=os.environ.get("FALLBACK_SHOP_PHONE", ""),
        # Không mượn FALLBACK_SHOP_PHONE làm mặc định: hai số khác vai trò, mượn vào thì
        # số chữa cháy sẽ đè lên số thật của shop khách đang đặt (_contact_phone).
        support_phone=os.environ.get("SUPPORT_PHONE", ""),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
        llm_timeout_nlu=float(os.environ.get("LLM_TIMEOUT_NLU", "8")),
        llm_timeout_nlg=float(os.environ.get("LLM_TIMEOUT_NLG", "6")),
        # Mặc định trỏ vào data/faq.md cạnh service -> cài xong là FAQ chạy luôn, không
        # phải khai báo gì. Đặt FAQ_CORPUS_PATH= (rỗng) để tắt.
        faq_corpus_path=os.environ.get("FAQ_CORPUS_PATH", _default_faq_path()),
        # Mặc định BẬT khi đã có router. Đặt FAQ_GENERATE=0 để quay lại trả nguyên văn —
        # dùng khi cần câu trả lời khớp từng chữ với data/faq.md (vd đang review nội dung).
        faq_generate=os.environ.get("FAQ_GENERATE", "1").strip().lower()
        not in ("0", "false", "no", "off", ""),
        llm_timeout_faq=float(os.environ.get("LLM_TIMEOUT_FAQ", "6")),
        rag_backend=os.environ.get("RAG_BACKEND", "bm25"),
        embedding_model=os.environ.get(
            "EMBEDDING_MODEL", "bkai-foundation-models/vietnamese-bi-encoder"),
        rerank_model=os.environ.get("RERANK_MODEL", "itdainb/PhoRanker"),
        retrieve_top_k=int(os.environ.get("RETRIEVE_TOP_K", "10")),
        final_top_k=int(os.environ.get("FINAL_TOP_K", "3")),
        vector_store_path=os.environ.get("VECTOR_STORE_PATH", _default_vector_path()),
    )
