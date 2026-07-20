const WEEKDAY_VI = ["CN", "Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7"];

/** Thứ tự cột lịch: T2 → CN (getDay(): 0 = CN). */
export const WEEKDAY_HEADERS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

/**
 * `new Date("2026-07-20")` bị parse theo UTC nên lệch ngày ở múi giờ âm.
 * Mọi chuyển đổi ISO <-> Date ở đây đều dùng giờ địa phương.
 */
export function parseIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function toIso(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function today(): Date {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
}

/** Cột của một ngày trong lưới T2→CN. */
export function mondayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

export function formatDateVi(iso: string): string {
  const date = parseIso(iso);
  const dd = String(date.getDate()).padStart(2, "0");
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  return `${WEEKDAY_VI[date.getDay()]}, ${dd}/${mm}/${date.getFullYear()}`;
}

export function formatDateShortVi(iso: string): string {
  const date = parseIso(iso);
  const dd = String(date.getDate()).padStart(2, "0");
  const mm = String(date.getMonth() + 1).padStart(2, "0");
  return `${WEEKDAY_VI[date.getDay()]} · ${dd}/${mm}`;
}

export function monthLabel(date: Date): string {
  return `Tháng ${date.getMonth() + 1}/${date.getFullYear()}`;
}

export function formatYen(amount: number): string {
  return `¥${amount.toLocaleString("en-US")}`;
}

export function formatDuration(minutes: number): string {
  return `${minutes} phút`;
}

/** 103 -> "1:43" (đồng hồ đếm ngược sửa nhanh — BR-17). */
export function formatCountdown(totalSeconds: number): string {
  const safe = Math.max(0, totalSeconds);
  const m = Math.floor(safe / 60);
  const s = safe % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** "14:30" -> 870 (số phút tính từ 00:00). */
export function timeToMinutes(time: string): number {
  const [h, m] = time.split(":").map(Number);
  return h * 60 + m;
}

/** 870 -> "14:30" */
export function minutesToTime(total: number): string {
  const h = Math.floor(total / 60) % 24;
  return `${String(h).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** "14:00" + 75 -> "15:15" */
export function addMinutesToTime(time: string, minutes: number): string {
  const [h, m] = time.split(":").map(Number);
  const total = h * 60 + m + minutes;
  return `${String(Math.floor(total / 60) % 24).padStart(2, "0")}:${String(
    total % 60,
  ).padStart(2, "0")}`;
}
