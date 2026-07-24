"use client";

/**
 * Chat widget đặt lịch (GĐ2) — ô chat nổi góc phải, nhúng trên trang khách.
 *
 * Bám chatbot-architecture.md §7: hội thoại + LỰA CHỌN DẠNG NÚT (giảm gõ, giảm NLU sai).
 * Câu chào của bot tự nói rõ "trợ lý AI" (minh bạch — APPI, §6.3.4). Mọi logic đặt chỗ nằm
 * ở service/BE; widget chỉ gửi text/nút và hiển thị `reply_text` + `ui.buttons` trả về.
 *
 * Tự ẩn ở khu admin/therapist/login — widget dành cho KHÁCH.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

import { sendChatMessage, type ChatButton } from "@/lib/chat";
import { Button, Chip, Spinner, TextInput, cx } from "@/components/ui";

type Msg = {
  id: number;
  role: "user" | "bot";
  text: string;
  buttons?: ChatButton[];
};

const HIDDEN_PREFIXES = ["/admin", "/therapist", "/login"];

export function ChatWidget() {
  const pathname = usePathname();

  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const cidRef = useRef<string | null>(null);   // conversation_id (chỉ sống trong phiên trang)
  const busyRef = useRef(false);                 // chặn gửi chồng lượt
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);

  const nextId = () => (idRef.current += 1);

  const send = useCallback(
    async (value: string, opts?: { display?: string; silentUser?: boolean }) => {
      if (busyRef.current) return;
      busyRef.current = true;
      setLoading(true);
      setError(null);

      if (!opts?.silentUser) {
        const display = opts?.display ?? value;
        setMessages((prev) => [...prev, { id: nextId(), role: "user", text: display }]);
      }

      try {
        const reply = await sendChatMessage({ conversation_id: cidRef.current, text: value });
        cidRef.current = reply.conversation_id;
        setMessages((prev) => [
          ...prev,
          { id: nextId(), role: "bot", text: reply.reply_text, buttons: reply.ui.buttons },
        ]);
        setDone(reply.done);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Có lỗi xảy ra, vui lòng thử lại.");
      } finally {
        busyRef.current = false;
        setLoading(false);
      }
    },
    [],
  );

  const reset = useCallback(() => {
    cidRef.current = null;
    setMessages([]);
    setDone(false);
    setError(null);
  }, []);

  // Mở lần đầu (chưa có tin) -> xin câu chào. Đóng/mở lại giữ nguyên hội thoại.
  useEffect(() => {
    if (open && messages.length === 0 && !busyRef.current) {
      void send("", { silentUser: true });
    }
  }, [open, messages.length, send]);

  // Luôn cuộn xuống tin mới nhất.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, loading, open]);

  if (pathname && HIDDEN_PREFIXES.some((p) => pathname.startsWith(p))) {
    return null;
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || loading) return;
    setInput("");
    void send(text, { display: text });
  }

  // Nút launcher khi đang đóng.
  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Mở trợ lý đặt lịch"
        className="fixed bottom-4 right-4 z-50 flex items-center gap-2 rounded-full border-[1.5px] border-frame bg-sel px-4 py-2.5 text-sm text-white shadow-[2px_3px_0_rgba(0,0,0,0.15)] transition-colors hover:bg-ink"
      >
        <span aria-hidden>💬</span>
        <b>Trợ lý đặt lịch</b>
      </button>
    );
  }

  const lastIndex = messages.length - 1;

  return (
    <div
      role="dialog"
      aria-label="Trợ lý đặt lịch AI"
      className="fixed bottom-4 right-4 z-50 flex max-h-[min(70vh,560px)] w-[min(92vw,380px)] flex-col overflow-hidden rounded-md border-[1.5px] border-frame bg-surface shadow-[2px_3px_0_rgba(0,0,0,0.15)]"
    >
      {/* Thanh tiêu đề — mô-típ .wt của wireframe */}
      <div className="flex items-center gap-2 border-b-[1.5px] border-frame bg-surface-2 px-3 py-2 text-xs text-ink-2">
        <span aria-hidden className="size-[9px] rounded-full border-[1.2px] border-ink-3" />
        <span aria-hidden className="size-[9px] rounded-full border-[1.2px] border-ink-3" />
        <b className="text-ink">Trợ lý đặt lịch</b>
        <span className="rounded-[3px] border border-accent-line bg-accent-soft px-1.5 py-0.5 text-[10px] text-accent-hover">
          AI
        </span>
        <span className="flex-1" />
        <button
          type="button"
          onClick={reset}
          className="rounded px-1.5 py-0.5 text-ink-3 transition-colors hover:bg-surface hover:text-ink"
          aria-label="Bắt đầu hội thoại mới"
          title="Đặt lịch mới"
        >
          ＋ Mới
        </button>
        <button
          type="button"
          onClick={() => setOpen(false)}
          className="rounded px-1.5 py-0.5 text-ink-3 transition-colors hover:bg-surface hover:text-ink"
          aria-label="Đóng trợ lý"
          title="Đóng"
        >
          ✕
        </button>
      </div>

      {/* Khung tin nhắn */}
      <div
        ref={scrollRef}
        role="log"
        aria-live="polite"
        className="flex-1 space-y-2 overflow-y-auto bg-canvas px-3 py-3"
      >
        {messages.map((m, i) => (
          <div key={m.id}>
            <div className={cx("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div
                className={cx(
                  "max-w-[85%] whitespace-pre-wrap break-words rounded-md px-3 py-2 text-sm leading-relaxed",
                  m.role === "user"
                    ? "bg-sel text-white"
                    : "border border-line-strong bg-surface text-ink",
                )}
              >
                {m.text}
              </div>
            </div>

            {/* Nút lựa chọn — chỉ ở tin bot MỚI NHẤT, ẩn khi đang chờ trả lời */}
            {m.role === "bot" && i === lastIndex && !loading && m.buttons && m.buttons.length > 0 ? (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {m.buttons.map((b) => (
                  <ChatChoice key={b.value} button={b} onPick={send} />
                ))}
              </div>
            ) : null}
          </div>
        ))}

        {loading ? (
          <div className="flex justify-start">
            <div className="flex items-center gap-2 rounded-md border border-line-strong bg-surface px-3 py-2 text-sm text-ink-2">
              <Spinner />
              Đang soạn…
            </div>
          </div>
        ) : null}

        {error ? (
          <div
            role="alert"
            className="rounded border-[1.2px] border-danger-line bg-danger-soft px-3 py-2 text-sm text-danger"
          >
            {error}
          </div>
        ) : null}
      </div>

      {/* Ô nhập */}
      <form onSubmit={onSubmit} className="flex items-center gap-2 border-t border-line bg-surface px-2 py-2">
        <TextInput
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder={done ? "Nhập để đặt tiếp, hoặc bấm ＋ Mới" : "Nhập tin nhắn…"}
          aria-label="Nội dung tin nhắn"
          disabled={loading}
        />
        <Button type="submit" variant="primary" disabled={loading || !input.trim()}>
          Gửi
        </Button>
      </form>
    </div>
  );
}

/** Một nút lựa chọn. Nút "gọi cửa hàng" thành link tel: cho bấm gọi luôn trên di động. */
function ChatChoice({
  button,
  onPick,
}: {
  button: ChatButton;
  onPick: (value: string, opts?: { display?: string }) => void;
}) {
  if (button.value === "handoff:call") {
    const phone = button.label.replace(/[^\d+]/g, "");
    if (phone) {
      return (
        <a
          href={`tel:${phone}`}
          className="rounded-[3px] border border-accent-line bg-accent-soft px-2 py-0.5 text-xs text-accent-hover transition-colors hover:bg-surface-2"
        >
          {button.label}
        </a>
      );
    }
  }
  return (
    <Chip onClick={() => onPick(button.value, { display: button.label })}>{button.label}</Chip>
  );
}
