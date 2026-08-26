"""Session Store — DD §2.4, Q5.

Mỗi `conversation_id`: state hiện tại, slots đã thu, vault PII, booking, lịch sử (đã mask).
Chính sách Q5: TTL sliding 30' (refresh mỗi lượt), rút vault sau cửa sổ sửa nhanh 2' (BR-17),
một Redis cho MVP. Mặc định dev dùng in-memory (không cần Redis) — cùng interface.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class Slots:
    """Tham số đặt chỗ gom dần qua hội thoại. `*_text` là gợi ý tự do từ NLU, PHẢI map về
    id qua tool response mới thành `*_id` (DD §2.3) — không tin nguyên văn."""

    shop_id: Optional[int] = None
    shop_name: Optional[str] = None       # cache tên shop đã chọn (đọc lại ở CONFIRM)
    date: Optional[str] = None            # YYYY-MM-DD
    party_size: Optional[int] = None      # 1..3
    duration: Optional[int] = None        # phút
    course_id: Optional[int] = None
    course_name: Optional[str] = None     # cache tên/thời lượng course đã chọn (đọc lại ở CONFIRM)
    # BR-10 (BA cập nhật): cả nhóm dùng CHUNG course và add-on -> một danh sách duy nhất,
    # gửi lặp lại cho từng reservation lúc tạo booking.
    addon_ids: list[int] = field(default_factory=list)
    # Tên add-on đã chọn — để đọc lại ở CONFIRM mà không phải gọi /services lần nữa
    # (giống course_name). Khách phải KIỂM được mình thêm gì trước khi chốt.
    addon_names: list[str] = field(default_factory=list)
    addons_decided: bool = False          # đã chốt add-on (kể cả chốt "không thêm gì")
    slot: Optional[str] = None            # "HH:MM" đã chốt
    therapist_id: Optional[int] = None    # khách chỉ định đích danh (chỉ party_size==1)
    therapist_gender: Optional[str] = None
    therapist_decided: bool = False       # đã chọn/đã bỏ qua chỉ định therapist
    wanted_time: Optional[str] = None     # "giờ mong muốn" — ưu tiên gợi ý quanh giờ này (§3.3)
    phone: Optional[str] = None           # placeholder {{phone_N}} (unmask khi gọi API)
    email: Optional[str] = None           # placeholder {{email_N}}
    contact_verified: bool = False        # đã qua POST /customers/lookup (chặn NG — BR-06)
    confirm: Optional[str] = None         # "yes" | "no" | None
    # gợi ý tự do (chưa map id)
    shop_text: Optional[str] = None
    course_text: Optional[str] = None
    therapist_text: Optional[str] = None
    addon_texts: list[str] = field(default_factory=list)  # tên add-on khách nói, chờ map id
    party_over: bool = False              # khách nói >3 người -> nhánh handoff (BR-14)


@dataclass
class Session:
    conversation_id: str
    state: str = "GREETING"
    slots: Slots = field(default_factory=Slots)
    vault: dict[str, str] = field(default_factory=dict)
    booking_code: Optional[str] = None
    edit_token: Optional[str] = None
    edit_token_expires_at: Optional[float] = None   # epoch giây
    editing: bool = False                           # đang sửa lịch đã đặt (UC-02, dùng UPDATE thay CREATE)
    # Sau cửa sổ nhanh 2' (vault đã rút PII), sửa/hủy cần khách nhập LẠI email để xác thực
    # (BR-15). Hai field này ghi nhớ "đang chờ email" và để làm gì.
    awaiting_edit_email: bool = False
    edit_email_for: str = ""                        # "update" | "cancel"
    shop_phone: Optional[str] = None                # cache để handoff (A5/A8)
    history: list[dict[str, str]] = field(default_factory=list)   # [{role, masked_text}]
    turn_count: int = 0
    # Số lượt hỏi NGOÀI PHẠM VI liên tiếp — chạm ngưỡng thì mời gọi cửa hàng, để khách và
    # bot không quay vòng "em chưa hỗ trợ được" mãi.
    offtopic_count: int = 0
    # Vệt intent đã nhận qua các lượt ("book", "ask_info:shops_open_at", "META:hủy lịch"…)
    # — in ở cuối mỗi khối log để đọc một khối là thấy cả đường đi của hội thoại.
    intent_trail: list[str] = field(default_factory=list)
    # shop_id mà câu trả lời tra cứu gần nhất đã nêu — ngữ cảnh cho câu hỏi nối tiếp.
    shop_shortlist: list[int] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        d = dict(d)
        d["slots"] = Slots(**d.get("slots", {}))
        return cls(**d)

    def maybe_drop_vault(self) -> None:
        """Rút vault sau cửa sổ sửa nhanh 2' khi phiên đã kết thúc (Q5). Giữ state/booking_code
        tới hết TTL để khách còn tra lại, nhưng PII thì xóa sớm."""
        terminal = self.state in ("DONE", "CANCELLED", "END", "HUMAN")
        expired = self.edit_token_expires_at is None or time.time() > self.edit_token_expires_at
        if terminal and expired and self.vault:
            self.vault = {}


class SessionStore(Protocol):
    def load(self, conversation_id: str) -> Optional[Session]: ...
    def save(self, session: Session) -> None: ...


class InMemorySessionStore:
    """MVP/dev — RAM tiến trình. Nhiều worker thì không chia sẻ (giống rate_limit shop_api);
    lên production đổi sang RedisSessionStore qua REDIS_URL."""

    def __init__(self, ttl_seconds: int = 1800):
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[float, dict]] = {}

    def load(self, conversation_id: str) -> Optional[Session]:
        rec = self._data.get(conversation_id)
        if rec is None:
            return None
        seen, payload = rec
        if time.time() - seen > self.ttl:          # hết TTL sliding
            self._data.pop(conversation_id, None)
            return None
        return Session.from_dict(payload)

    def save(self, session: Session) -> None:
        self._data[session.conversation_id] = (time.time(), session.to_dict())


class RedisSessionStore:
    """Production (Q5). Import redis lười — không cài redis vẫn chạy được nếu không chọn."""

    def __init__(self, redis_url: str, ttl_seconds: int = 1800):
        import redis  # noqa: F401 — chỉ cần khi thực sự chọn Redis

        self._r = redis.Redis.from_url(redis_url, decode_responses=True)
        self.ttl = ttl_seconds

    def _key(self, cid: str) -> str:
        return f"chat:session:{cid}"

    def load(self, conversation_id: str) -> Optional[Session]:
        raw = self._r.get(self._key(conversation_id))
        if raw is None:
            return None
        return Session.from_dict(json.loads(raw))

    def save(self, session: Session) -> None:
        # EX = TTL sliding: mỗi lần save gia hạn thêm ttl (refresh mỗi lượt — Q5).
        self._r.set(
            self._key(session.conversation_id),
            json.dumps(session.to_dict(), ensure_ascii=False),
            ex=self.ttl,
        )


def build_store(redis_url: str, ttl_seconds: int) -> SessionStore:
    if redis_url:
        return RedisSessionStore(redis_url, ttl_seconds)
    return InMemorySessionStore(ttl_seconds)
