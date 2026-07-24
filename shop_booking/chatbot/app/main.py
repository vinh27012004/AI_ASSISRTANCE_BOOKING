"""Web layer — Flask. Endpoint đối ngoại DUY NHẤT: POST /chat/message (DD §2.1, Q3).

Schema tối thiểu MVP, KHÔNG streaming: {conversation_id, text, lang} -> {conversation_id,
reply_text, state, ui.buttons[], done}. Nâng lên SSE sau không phá schema (chỉ đổi content-type).
"""

from __future__ import annotations

from dataclasses import asdict

from flask import Flask, jsonify, request

from app.config import load_settings
from app.llm_client import build_llm
from app.orchestrator import Orchestrator
from app.session import build_store
from app.shop_api_client import ShopApiClient


def create_app() -> Flask:
    app = Flask(__name__)
    settings = load_settings()

    store = build_store(settings.redis_url, settings.session_ttl_seconds)
    api = ShopApiClient(settings.shop_api_base_url)
    llm = build_llm(settings)
    orch = Orchestrator(store, api, llm, settings)
    app.extensions["orchestrator"] = orch

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "llm": "router" if settings.use_real_llm else "fake",
            "session": "redis" if settings.use_redis else "memory",
        })

    @app.post("/chat/message")
    def chat_message():
        data = request.get_json(silent=True) or {}
        text = data.get("text", "")
        if not isinstance(text, str):
            return jsonify({"error": {"code": "VALIDATION_ERROR",
                                      "message": "Trường 'text' phải là chuỗi."}}), 400

        reply = orch.handle_turn(
            conversation_id=data.get("conversation_id"),
            user_text=text,
            lang_hint=data.get("lang"),
        )
        return jsonify(asdict(reply)), 200

    return app


app = create_app()
