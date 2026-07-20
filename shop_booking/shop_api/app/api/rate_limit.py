"""Rate limit tối giản cho nhóm BOOKING-MANAGE.

Vì sao cần: `booking_code` có format đoán được (`{yyyyMMdd}-{shop_code}-{random 4-6}`).
Quyết định thiết kế #5 trả 404 thay 403 để không tiết lộ mã nào tồn tại — nhưng nếu cho
thử không giới hạn thì việc giấu đó chỉ làm kẻ dò chậm lại, không chặn được.

Giới hạn của bản này (chấp nhận ở giai đoạn dev, phải thay khi lên production):
  * Lưu trong RAM của tiến trình -> chạy nhiều worker thì mỗi worker một bộ đếm,
    hạn thực tế = max_calls × số worker. Restart là mất sạch.
  * Production nên dùng Flask-Limiter + Redis để dùng chung bộ đếm.
"""

import time
from collections import defaultdict, deque
from functools import wraps

from flask import request

from app.api.errors import APIError

RATE_LIMITED_MESSAGE = "Bạn thao tác quá nhanh. Vui lòng thử lại sau ít phút."

# key -> deque[timestamp]
_hits: dict[str, deque[float]] = defaultdict(deque)

# Dọn rác khi số key phình quá ngưỡng, tránh rò rỉ bộ nhớ theo số IP đã ghé.
_MAX_TRACKED_KEYS = 10_000


def _client_ip() -> str:
    """IP client. X-Forwarded-For chỉ đáng tin khi app chạy sau proxy mình kiểm soát —
    client tự đặt header này được, nên khi deploy phải bọc ProxyFix và chỉ tin proxy nhà."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote_addr or "unknown"


def _prune(now: float, window: int) -> None:
    if len(_hits) <= _MAX_TRACKED_KEYS:
        return
    for key in [k for k, v in _hits.items() if not v or now - v[-1] > window]:
        _hits.pop(key, None)


def rate_limit(max_calls: int, per_seconds: int):
    """Chặn quá `max_calls` request trong `per_seconds` giây, tính theo IP + endpoint."""

    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            now = time.monotonic()
            key = f"{view.__name__}:{_client_ip()}"
            bucket = _hits[key]

            # Bỏ các hit đã ra khỏi cửa sổ (sliding window).
            while bucket and now - bucket[0] > per_seconds:
                bucket.popleft()

            if len(bucket) >= max_calls:
                raise APIError(429, "RATE_LIMITED", RATE_LIMITED_MESSAGE)

            bucket.append(now)
            _prune(now, per_seconds)
            return view(*args, **kwargs)

        return wrapper

    return decorator


def reset_rate_limits() -> None:
    """Chỉ dùng cho test — bộ đếm sống xuyên request nên test sẽ dính lẫn nhau."""
    _hits.clear()
