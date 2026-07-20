import type { BookingStatus } from "@/lib/types";
import { cx } from "@/components/ui";

const STATUS_LABEL: Record<BookingStatus, string> = {
  pending: "Chờ xác nhận",
  confirmed: "Đã xác nhận",
  cancelled: "Đã huỷ",
  completed: "Đã hoàn thành",
  no_show: "Không đến",
};

const STATUS_TONE: Record<BookingStatus, string> = {
  pending: "border-warn-line bg-warn-soft text-warn",
  confirmed: "border-success-line bg-success-soft text-success",
  cancelled: "border-danger-line bg-danger-soft text-danger",
  completed: "border-line-strong bg-surface-2 text-ink-2",
  no_show: "border-danger-line bg-danger-soft text-danger",
};

export function BookingStatusBadge({ status }: { status: BookingStatus }) {
  return (
    <span
      className={cx(
        "inline-block rounded-lg border px-2.5 py-1 text-xs font-medium",
        STATUS_TONE[status],
      )}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
