"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import type { Booking, BookingCreated } from "@/lib/types";
import { Alert, Button, buttonClass } from "@/components/ui";
import { formatCountdown, formatDateVi, formatYen } from "@/lib/format";
import { bookingTotals } from "./booking-detail";
import { ReschedulePanel } from "./reschedule-panel";

/** Message chuẩn của EDIT_TOKEN_EXPIRED (BR-17) — hiện khi hết 2 phút. */
const EDIT_TOKEN_EXPIRED_MESSAGE =
  "Phiên chỉnh sửa nhanh đã hết hạn. Vui lòng dùng trang Quản lý đặt chỗ với mã đặt chỗ và email của bạn.";

/**
 * Đếm ngược tới mốc thời gian cố định (không trừ dần), nên tab bị throttle hay
 * ngủ rồi quay lại vẫn ra đúng số giây còn lại.
 */
function useCountdown(seconds: number) {
  const [deadline] = useState(() => Date.now() + seconds * 1000);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (Date.now() >= deadline) return;

    const timer = setInterval(() => {
      const tick = Date.now();
      setNow(tick);
      if (tick >= deadline) clearInterval(timer);
    }, 1000);

    return () => clearInterval(timer);
  }, [deadline]);

  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

export function StepSuccess({
  booking,
  onRestart,
}: {
  booking: BookingCreated;
  onRestart: () => void;
}) {
  const remaining = useCountdown(booking.edit_token_expires_in);
  const expired = remaining <= 0;

  // PATCH trả Booking đã cập nhật (không cấp edit_token mới) — giữ bản mới nhất
  // để màn hình hiện đúng giờ vừa sửa.
  const [current, setCurrent] = useState<Booking>(booking);
  const [editing, setEditing] = useState(false);
  const [edited, setEdited] = useState(false);

  const { total } = bookingTotals(current);

  // Hết 2 phút giữa chừng thì đóng form — BE cũng sẽ từ chối PATCH lúc này.
  if (editing && expired) setEditing(false);

  if (editing) {
    return (
      <>
        <div className="border-b border-dashed border-line bg-accent-soft px-4 py-2 text-sm">
          <span className="font-medium text-accent-hover">Sửa nhanh</span>
          <span className="ml-2 tabular-nums text-ink-2">
            còn {formatCountdown(remaining)}
          </span>
        </div>

        <ReschedulePanel
          booking={current}
          editToken={booking.edit_token}
          onCancel={() => setEditing(false)}
          onDone={(next) => {
            setCurrent(next);
            setEditing(false);
            setEdited(true);
          }}
        />
      </>
    );
  }

  return (
    <>
      <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
        <span className="flex size-10 items-center justify-center rounded-full border-2 border-success text-xl text-success">
          ✓
        </span>
        <h2 className="text-base font-bold">Đặt chỗ thành công!</h2>

        <p className="text-xs text-ink-2">Mã đặt chỗ của bạn (đã gửi qua email):</p>
        {/* .box mono chữ giãn như wireframe 05 */}
        <div className="rounded border border-line-strong bg-surface px-4 py-1.5 font-mono text-base font-semibold tracking-widest">
          {current.booking_code}
        </div>

        <p className="max-w-md text-xs text-ink-2">
          {formatDateVi(current.date)} · {current.start_time} ·{" "}
          {current.shop.name} · {current.party_size} người ·{" "}
          <span className="tabular-nums">{formatYen(total)}</span>
        </p>
        <p className="text-xs text-ink-3">
          Dùng mã này để sửa hoặc hủy ở trang Quản lý đặt chỗ.
        </p>

        {edited ? (
          <Alert tone="success" className="mt-1 text-left">
            Đã đổi giờ hẹn. Email xác nhận đã được gửi cho bạn.
          </Alert>
        ) : null}
      </div>

      {/* BR-17 — cửa sổ sửa nhanh 2 phút bằng edit_token BE cấp lúc tạo.
          Dải nền xanh nhạt + chip đồng hồ + nút viền xanh, đúng wireframe 05. */}
      <div className="border-t border-dashed border-line bg-accent-soft px-4 py-3">
        {expired ? (
          <p className="text-sm text-ink-2">{EDIT_TOKEN_EXPIRED_MESSAGE}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-[3px] border border-accent bg-accent-soft px-2 py-0.5 text-xs tabular-nums text-accent-hover">
              ⏱ Sửa nhanh còn {formatCountdown(remaining)}
            </span>
            <span className="min-w-0 flex-1 text-xs text-ink-2">
              Phát hiện nhập nhầm? Sửa ngay không cần xác thực lại.
            </span>
            <Button variant="accent" onClick={() => setEditing(true)}>
              Sửa nhanh
            </Button>
          </div>
        )}
      </div>

      <div className="flex flex-wrap justify-center gap-2 border-t border-dashed border-line px-4 py-3">
        <Link href="/booking/manage" className={buttonClass("outline")}>
          Quản lý đặt chỗ
        </Link>
        <Button onClick={onRestart}>Về trang đặt lịch</Button>
      </div>
    </>
  );
}
