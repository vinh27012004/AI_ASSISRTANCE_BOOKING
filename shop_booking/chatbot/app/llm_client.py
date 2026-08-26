"""LLM adapter — router OpenAI-compatible (chatbot-architecture.md §6.3, Q4/Q7).

Đổi provider = đổi `base_url` + `api_key` (không đổi code). Dùng urllib (stdlib) để lõi
không kéo thêm phụ thuộc; production có thể thay bằng httpx nếu cần pool/async.

`build_llm()` trả None khi chưa cấu hình router -> NLU/NLG rơi về nhánh offline (fake) của
chúng, nên service chạy/test được không cần LLM (mẹo test §9)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
import uuid

from app.config import Settings

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class RealLLMClient:
    """Gọi POST {base_url}/chat/completions kiểu OpenAI. Không giữ business logic."""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 20.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 512, response_json: bool = False,
                 timeout: float | None = None) -> str:
        """`timeout` cho phép mỗi chỗ gọi tự đặt hạn riêng: NLU chặn cả lượt chat nên phải
        ngắn (có rule-based đỡ), NLG chỉ còn GREETING/REPROMPT nên còn ngắn hơn. Không
        truyền thì dùng self.timeout."""
        # call_id nối dòng request <-> response cùng 1 lời gọi trong log (giống
        # ShopApiClient._request) — nhiều hội thoại chạy đồng thời thì log các lượt xen kẽ nhau.
        call_id = uuid.uuid4().hex[:8]
        timeout = self.timeout if timeout is None else timeout
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            # Nhiều router (vd cái đang dùng) MẶC ĐỊNH stream SSE -> body không phải JSON
            # một cục. Xin non-stream; nếu router vẫn stream, _extract_content ghép chunk.
            "stream": False,
        }
        if response_json:
            # Router nào không hỗ trợ sẽ bỏ qua field này — vẫn parse JSON ở nlu.py.
            payload["response_format"] = {"type": "json_object"}

        # system/user đã được mask PII từ trước khi tới đây (app/pii.py) nên log nguyên văn
        # an toàn. KHÔNG log api_key/header Authorization — đó là secret gọi router.
        # DEBUG chứ không INFO: system prompt ~2000 ký tự, in mỗi lời gọi thì log không đọc
        # nổi. Tóm tắt lượt nằm ở app/turnlog.py; cần soi prompt thì LOG_LEVEL=DEBUG.
        logger.debug("llm -> [%s] model=%s temperature=%s max_tokens=%s timeout=%s system=%s user=%s",
                     call_id, self.model, temperature, max_tokens, timeout, system, user)

        req = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            logger.warning("llm <- [%s] HTTP %s: %s", call_id, e.code, detail)
            raise LLMError(f"HTTP {e.code}: {detail}") from e
        except OSError as e:
            # OSError bao trùm cả URLError/TimeoutError (đều kế thừa OSError) LẪN các lỗi
            # socket urllib KHÔNG bọc thành URLError (vd ConnectionResetError khi bị ngắt
            # kết nối giữa lúc đọc response ở http.client.getresponse() — urllib chỉ bọc
            # OSError thành URLError lúc GỬI request, không bọc lúc ĐỌC response) — nếu chỉ
            # bắt (URLError, TimeoutError) thì ConnectionResetError lọt qua thành lỗi 500
            # không rõ nguyên nhân ở /chat/message.
            logger.error("llm <- [%s] lỗi kết nối: %s", call_id, e)
            raise LLMError(str(e)) from e

        logger.debug("llm <- [%s] raw=%s", call_id, raw)
        try:
            return _extract_content(raw)
        except LLMError as e:
            logger.warning("llm <- [%s] parse lỗi: %s raw[:200]=%r", call_id, e, raw[:200])
            raise


def _extract_content(raw: str) -> str:
    """Lấy text trả lời từ body — chịu được CẢ hai dạng:
    - JSON một cục (non-stream): choices[0].message.content
    - SSE stream ('data: {...}' mỗi dòng): ghép choices[0].delta.content
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    if raw.startswith("data:"):                       # router vẫn stream SSE
        parts: list[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
                delta = chunk["choices"][0].get("delta", {})
            except (ValueError, KeyError, IndexError, TypeError):
                continue
            if delta.get("content"):
                parts.append(delta["content"])
        return "".join(parts).strip()

    try:                                               # JSON non-stream
        obj = json.loads(raw)
        return (obj["choices"][0]["message"]["content"] or "").strip()
    except (ValueError, KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Định dạng phản hồi LLM bất thường: {raw[:200]!r}") from e


def build_llm(settings: Settings) -> RealLLMClient | None:
    if not settings.use_real_llm:
        return None
    return RealLLMClient(settings.llm_base_url, settings.llm_api_key, settings.llm_model)
