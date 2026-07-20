"use client";

import { useSyncExternalStore } from "react";

const KEY = "admin_selected_shop_id";
/** Layout admin bắn event này sau khi ghi localStorage (đổi shop trong cùng tab). */
const EVENT = "admin_shop_changed";

function subscribe(onChange: () => void) {
  window.addEventListener(EVENT, onChange);
  // `storage` chỉ bắn ở CÁC TAB KHÁC — nhờ vậy mở hai tab admin, đổi shop ở tab
  // này thì tab kia cũng theo, không còn hiển thị dữ liệu của shop cũ.
  window.addEventListener("storage", onChange);
  return () => {
    window.removeEventListener(EVENT, onChange);
    window.removeEventListener("storage", onChange);
  };
}

function getSnapshot(): number | null {
  const raw = localStorage.getItem(KEY);
  if (!raw) return null;
  const id = Number(raw);
  // localStorage là chuỗi do người dùng sửa được — chặn NaN chui vào query string.
  return Number.isFinite(id) ? id : null;
}

/** Server không có localStorage; trả null để HTML dựng sẵn khớp với lần render đầu ở client. */
function getServerSnapshot(): number | null {
  return null;
}

/**
 * Shop admin đang chọn, đọc từ localStorage.
 *
 * Dùng useSyncExternalStore thay vì useState + useEffect: localStorage cộng với
 * event là một external store đúng nghĩa. Cách cũ (mỗi trang admin tự chép một bản
 * `loadSelectedShopId` rồi setState trong effect) khiến mỗi lần vào trang phải render
 * thừa một vòng với `shopId = null`, và bốn bản chép đó phải sửa cùng lúc mỗi khi
 * đổi logic.
 *
 * Giá trị trả về là number|null (nguyên thuỷ) nên so sánh theo giá trị — getSnapshot
 * trả object mới mỗi lần gọi sẽ làm React lặp vô hạn.
 */
export function useSelectedShopId(): number | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
