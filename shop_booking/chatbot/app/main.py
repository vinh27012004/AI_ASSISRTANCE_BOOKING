"""Web layer — Flask. Endpoint đối ngoại DUY NHẤT: POST /chat/message (DD §2.1, Q3).

Schema tối thiểu MVP, KHÔNG streaming: {conversation_id, text} -> {conversation_id,
reply_text, state, ui.buttons[] (luôn rỗng — giữ cho FE cũ), done}. Nâng lên SSE sau không
phá schema (chỉ đổi content-type).
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict

from flask import Flask, jsonify, request

from app.config import load_settings
from app.llm_client import build_llm
from app.orchestrator import Orchestrator
from app.session import build_store
from app.shop_api_client import ShopApiClient

_CHATBOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_FILE = os.path.join(_CHATBOT_DIR, "logs", "chatbot.log")


def _configure_logging(level: str) -> None:
    """Log request/response gọi shop_api (app/shop_api_client.py) ra CẢ console lẫn file,
    để xem lại được khi debug dữ liệu sai. force=True để thắng handler mặc định Flask/Werkzeug
    có thể đã gắn sẵn trước khi hàm này chạy."""
    os.makedirs(os.path.dirname(_LOG_FILE), exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(_LOG_FILE, encoding="utf-8")],
        force=True,
    )


def create_app() -> Flask:
    app = Flask(__name__)
    settings = load_settings()
    _configure_logging(settings.log_level)

    store = build_store(settings.redis_url, settings.session_ttl_seconds)
    api = ShopApiClient(settings.shop_api_base_url)
    llm = build_llm(settings)
    orch = Orchestrator(store, api, llm, settings)
    app.extensions["orchestrator"] = orch

    @app.get("/health")
    def health():
        # `faq` cho biết corpus nạp được mấy mục — deploy quên copy data/faq.md thì thấy 0
        # ngay ở đây, thay vì phải đợi khách hỏi mới biết bot mất khả năng trả lời.
        retriever = getattr(orch, "_faq_retriever", None)
        return jsonify({
            "status": "ok",
            "llm": "router" if settings.use_real_llm else "fake",
            "session": "redis" if settings.use_redis else "memory",
            "faq": {
                "chunks": len(retriever.chunks) if retriever else 0,
                # Báo backend THỰC TẾ đang chạy, không phải cái đặt trong .env: yêu cầu
                # hybrid mà thiếu gói thì retrieval lùi về bm25 trong im lặng, ở đây là chỗ
                # duy nhất thấy được điều đó mà không phải đi đọc log.
                "retrieval": ("hybrid" if (retriever and retriever.is_hybrid) else "bm25"),
                "retrieval_yeu_cau": settings.rag_backend,
                # Bước sinh chỉ chạy khi CÓ router VÀ cờ bật -> nói rõ đang ở chế độ nào,
                # khỏi phải đoán vì sao câu trả lời khớp/không khớp từng chữ với faq.md.
                "generation": ("llm" if (settings.faq_generate and settings.use_real_llm)
                               else "nguyên văn"),
            },
        })

    @app.post("/chat/message")
    def chat_message():
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        if not isinstance(text, str):
            return jsonify({"error": {"code": "VALIDATION_ERROR",
                                      "message": "Trường 'text' phải là chuỗi."}}), 400

        # Trường 'lang' cũ (nếu FE còn gửi) được bỏ qua — bot chỉ phục vụ tiếng Việt.
        reply = orch.handle_turn(
            conversation_id=data.get("conversation_id"),
            user_text=text,
        )
        return jsonify(asdict(reply)), 200

    return app


app = create_app()
