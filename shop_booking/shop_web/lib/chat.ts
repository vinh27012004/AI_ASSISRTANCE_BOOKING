/**
 * Client cho chatbot service (GĐ2). Gọi same-origin `/api/chat/message` (rewrite trong
 * next.config.ts trỏ sang service cổng 5100) — cùng nguyên tắc no-CORS như `lib/api.ts`.
 *
 * Widget là "client thứ hai" của luồng đặt chỗ, nhưng KHÔNG gọi thẳng shop_api: mọi thứ
 * đi qua chatbot service, service mới là bên gọi shop_api (kèm X-Api-Key + Idempotency-Key).
 */

const CHAT_BASE = "/api/chat";

/** Nút lựa chọn do bot trả về; `value` là token (vd "course:3") gửi lại nguyên văn. */
export type ChatButton = { label: string; value: string };

/** Response của POST /chat/message (DD §2.1). */
export type ChatReply = {
  conversation_id: string;
  reply_text: string;
  state: string;
  ui: { buttons: ChatButton[] };
  done: boolean;
};

const FALLBACK = "Trợ lý đang gặp sự cố. Vui lòng thử lại sau ít phút.";

export function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError";
}

export async function sendChatMessage(
  payload: { conversation_id: string | null; text: string; lang?: string | null },
  signal?: AbortSignal,
): Promise<ChatReply> {
  let res: Response;
  try {
    res = await fetch(`${CHAT_BASE}/message`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal,
    });
  } catch (error) {
    if (isAbort(error)) throw error;
    throw new Error("Không kết nối được tới trợ lý. Vui lòng kiểm tra mạng và thử lại.");
  }

  const body: unknown = await res.json().catch(() => null);

  if (!res.ok) {
    const message =
      (body as { error?: { message?: string } } | null)?.error?.message ?? FALLBACK;
    throw new Error(message);
  }

  return body as ChatReply;
}
