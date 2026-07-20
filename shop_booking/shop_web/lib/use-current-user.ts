"use client";

import { useMemo, useSyncExternalStore } from "react";
import type { StoredUser } from "./api";

function subscribe(onChange: () => void) {
  // Đăng xuất ở tab khác thì tên ở tab này cũng phải biến mất theo.
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/**
 * Trả CHUỖI thô chứ không phải object đã parse — cùng lý do với `auth-guard.tsx`:
 * getSnapshot phải trả giá trị so sánh được bằng ===, trả object mới mỗi lần gọi
 * là React re-render vô hạn.
 */
function getSnapshot(): string | null {
  return localStorage.getItem("user_info");
}

function getServerSnapshot(): string | null {
  return null;
}

/** Người đang đăng nhập, `null` khi chưa đăng nhập hoặc chưa hydrate xong. */
export function useCurrentUser(): StoredUser | null {
  const raw = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  return useMemo(() => {
    if (!raw) return null;
    try {
      return JSON.parse(raw) as StoredUser;
    } catch {
      // user_info hỏng (người dùng tự sửa localStorage) — coi như không có tên.
      return null;
    }
  }, [raw]);
}
