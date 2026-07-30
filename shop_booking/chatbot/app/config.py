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
        # SUPPORT_PHONE riêng; chưa đặt thì lấy FALLBACK_SHOP_PHONE (số env sẵn có).
        support_phone=os.environ.get("SUPPORT_PHONE", os.environ.get("FALLBACK_SHOP_PHONE", "")),
        log_level=os.environ.get("LOG_LEVEL", "INFO"),
    )
