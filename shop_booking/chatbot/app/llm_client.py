"""LLM adapter — router OpenAI-compatible (chatbot-architecture.md §6.3, Q4/Q7).

Đổi provider = đổi `base_url` + `api_key` (không đổi code). Dùng urllib (stdlib) để lõi
không kéo thêm phụ thuộc; production có thể thay bằng httpx nếu cần pool/async.

`build_llm()` trả None khi chưa cấu hình router -> NLU/NLG rơi về nhánh offline (fake) của
chúng, nên service chạy/test được không cần LLM (mẹo test §9)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app.config import Settings


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
                 max_tokens: int = 512, response_json: bool = False) -> str:
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise LLMError(f"HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(str(e)) from e

        return _extract_content(raw)


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
