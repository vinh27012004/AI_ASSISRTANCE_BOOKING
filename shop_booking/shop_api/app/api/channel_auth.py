"""Middleware xác thực kênh client GĐ2 (chatbot) — api-design §7.2, DD_chatbot Q2.

Luồng đặt chỗ vốn public cho FE web. Khi có chatbot gọi tự động cần thêm một lớp:
  (a) nhận diện request đến từ kênh chatbot (tách log/metrics),
  (b) chặn lạm dụng RIÊNG cho kênh — độc lập rate-limit nhóm BOOKING-MANAGE (10/60s)
      và login (5/60s). Ba bộ đếm khác nhau.

Quy tắc (chạy như `before_request` của api_bp — phủ mọi /api/v1/*):
  1. Không có header X-Api-Key  → coi là kênh public (FE web) → xử lý như GĐ1.
  2. Có X-Api-Key: hash → tra channel_api_key. Không khớp / is_active=false → 401.
  3. Khớp → đếm request theo key trong cửa sổ 60s. Vượt rate_limit_per_min → 429
     kèm details.retry_after. Cập nhật last_used_at.

Giới hạn bản MVP (giống rate_limit.py): bộ đếm nằm trong RAM tiến trình → chạy nhiều
worker thì mỗi worker một bộ đếm (hạn thực = rate_limit × số worker), restart là mất.
Production dùng Redis dùng chung (§7.2).
"""

import hashlib
import time
from collections import defaultdict, deque
from datetime import datetime

from flask import g, request

from app.extensions import db
from app.api.errors import APIError
from app.models.shop import ChannelApiKey

CHANNEL_API_KEY_HEADER = "X-Api-Key"
_WINDOW_SECONDS = 60

# 429 message theo catalog api-design §0.2 (429 RATE_LIMITED).
RATE_LIMITED_MESSAGE = "Bạn thao tác quá nhanh, vui lòng thử lại sau giây lát."
CHANNEL_UNAUTHORIZED_MESSAGE = "Kênh gọi API không được xác thực."

# channel_api_key.id -> deque[timestamp] (sliding window 60s)
_hits: dict[int, deque[float]] = defaultdict(deque)


def hash_api_key(raw_key: str) -> str:
    """SHA-256 hex — tất định để tra ngược bằng hash. Không bao giờ lưu key thô."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def _enforce_rate_limit(channel: ChannelApiKey) -> None:
    now = time.monotonic()
    bucket = _hits[channel.id]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= channel.rate_limit_per_min:
        # Giây còn lại tới khi hit cũ nhất rời cửa sổ → client biết chờ bao lâu.
        retry_after = max(1, int(_WINDOW_SECONDS - (now - bucket[0])) + 1)
        raise APIError(
            429, "RATE_LIMITED", RATE_LIMITED_MESSAGE, {"retry_after": retry_after}
        )

    bucket.append(now)


def enforce_channel_auth():
    """`before_request` cho api_bp. Đặt `g.channel` = kênh (hoặc None nếu public)."""
    raw_key = request.headers.get(CHANNEL_API_KEY_HEADER)
    if not raw_key:
        g.channel = None
        return  # kênh public (FE web): xử lý y hệt GĐ1

    channel = ChannelApiKey.query.filter_by(
        key_hash=hash_api_key(raw_key), is_active=True
    ).first()
    if channel is None:
        raise APIError(401, "CHANNEL_UNAUTHORIZED", CHANNEL_UNAUTHORIZED_MESSAGE)

    _enforce_rate_limit(channel)

    # last_used_at — best-effort, commit riêng để không kẹt vào transaction của route
    # (route sau đó mở transaction mới). Ghi hỏng thì bỏ qua, không được chặn request.
    try:
        channel.last_used_at = datetime.now()
        db.session.commit()
    except Exception:
        db.session.rollback()

    g.channel = channel


def reset_channel_rate_limits() -> None:
    """Chỉ dùng cho test — bộ đếm sống xuyên request nên test sẽ dính lẫn nhau."""
    _hits.clear()
