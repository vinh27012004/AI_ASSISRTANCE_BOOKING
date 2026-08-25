"""Log gom theo LƯỢT — mỗi lượt chat là MỘT bản ghi nhiều dòng, đọc từ trên xuống là thấy
đủ: khách nói gì -> từng bước xử lý -> bot trả gì.

Trước đây mỗi module tự log một dòng INFO rời rạc, lại kèm NGUYÊN system prompt (~2000 ký
tự) mỗi lời gọi LLM, nên log vừa dài vừa không cho biết lượt đó rẽ nhánh nào (đã có lúc
phải đi so chuỗi prompt mới tìm ra bug). Nay:

- INFO: một khối/lượt, gọn, đủ để đọc luồng.
- DEBUG: chi tiết thô (system prompt, raw response, params/body của shop_api). Bật bằng
  LOG_LEVEL=DEBUG trong .env.

Khối được phát ra MỘT lần ở cuối lượt nên không bị xen kẽ khi nhiều hội thoại chạy song
song. Buffer nằm trong threading.local -> mỗi request một buffer riêng.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger("app.turn")

_NL = chr(10)
_local = threading.local()
_MAX_TEXT = 300          # câu dài hơn thì cắt, log để ĐỌC chứ không để lưu trữ đủ


class TurnLog:
    def __init__(self, conversation_id: str, turn: int, state_in: str):
        self.cid = conversation_id
        self.turn = turn
        self.state_in = state_in
        self.t0 = time.time()
        self.rows: list[tuple[str, str]] = []

    def render(self, state_out: str, slots: str) -> str:
        secs = time.time() - self.t0
        out = ["", f"┌─ LƯỢT {self.turn} · conv={self.cid} · vào state={self.state_in}"]
        for tag, text in self.rows:
            out.append(f"│ {tag:<7}{text}")
        tail = f"└─ ra state={state_out} · {secs:.2f}s"
        if slots:
            tail += f" · {slots}"
        out.append(tail)
        return _NL.join(out)


# --------------------------------------------------------------------------- #
#  Vòng đời                                                                    #
# --------------------------------------------------------------------------- #

def start(conversation_id: str, turn: int, state_in: str) -> None:
    _local.turn = TurnLog(conversation_id, turn, state_in)


def current() -> TurnLog | None:
    return getattr(_local, "turn", None)


def finish(state_out: str = "?", slots: str = "") -> None:
    tl = current()
    if tl is None:
        return
    _local.turn = None
    logger.info("%s", tl.render(state_out, slots))


def _row(tag: str, text: str) -> None:
    """No-op khi không ở trong một lượt (test gọi thẳng hàm, hoặc client dùng độc lập)."""
    tl = current()
    if tl is not None:
        tl.rows.append((tag, text))


def _clip(text: str) -> str:
    one = " ⏎ ".join((text or "").splitlines())
    return one if len(one) <= _MAX_TEXT else one[:_MAX_TEXT] + " …"


# --------------------------------------------------------------------------- #
#  Từng bước (khớp 6 bước của orchestrator)                                    #
# --------------------------------------------------------------------------- #

def inp(text: str) -> None:
    _row("IN", _clip(text))


def nlu(source: str, secs: float, intent: str, question_type, entities: dict) -> None:
    ent = " ".join(f"{k}={v}" for k, v in (entities or {}).items()
                   if v not in (None, [], "", "null"))
    line = f"{source} {secs:.2f}s · intent={intent} · qt={question_type or '-'}"
    _row("①NLU", line + (f" · {ent}" if ent else ""))


def lane(name: str, detail: str = "") -> None:
    """TASK (điền đơn) · QUERY (hỏi thông tin) · META (sửa/hủy/gặp người)."""
    _row("LANE", name + (f" → {detail}" if detail else ""))


def merge(changes: list[str]) -> None:
    _row("②GỘP", " · ".join(changes) if changes else "(không đổi ô nào)")


def state(prev: str, new: str) -> None:
    _row("③STATE", f"{prev} → {new}" if prev != new else f"{new} (giữ)")


def api(method: str, path: str, status, ms: float) -> None:
    _row("④API", f"{method} {path} → {status} {ms:.0f}ms")


def nlg(key: str, via: str, secs: float | None = None) -> None:
    _row("⑥NLG", f"{key} · {via}" + (f" {secs:.2f}s" if secs is not None else ""))


def note(text: str) -> None:
    _row("·", text)


def out(text: str) -> None:
    _row("OUT", _clip(text))
