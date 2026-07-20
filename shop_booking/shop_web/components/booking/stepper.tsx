import type { ReactNode } from "react";
import { Card, WindowBar, cx } from "@/components/ui";

/** Nhãn chip đúng như thanh điều hướng của wireframe. */
export const STEP_LABELS = [
  "1 Cửa hàng",
  "2 Booking",
  "3 Thông tin",
  "4 Xác nhận",
  "5 Xong",
] as const;

/**
 * Dãy chip bước như wireframe: chip nền tối = bước hiện tại. Bước ĐÃ QUA bấm
 * được để quay lại; bước phía trước thì không (dữ liệu chưa đủ), và sau khi đặt
 * xong (bước 5) không quay lại được nữa.
 */
export function Stepper({
  current,
  onNavigate,
}: {
  current: number;
  onNavigate?: (step: number) => void;
}) {
  return (
    <ol className="flex flex-wrap items-center justify-center gap-2">
      {STEP_LABELS.map((label, index) => {
        const step = index + 1;
        const active = step === current;
        const canGo = Boolean(onNavigate) && step < current && current < 5;

        return (
          <li key={label} aria-current={active ? "step" : undefined}>
            {canGo ? (
              <button
                type="button"
                onClick={() => onNavigate!(step)}
                className="rounded-[3px] border border-line-strong bg-surface px-3 py-1 text-xs text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              >
                {label}
              </button>
            ) : (
              <span
                className={cx(
                  "inline-block rounded-[3px] border px-3 py-1 text-xs",
                  active
                    ? "border-sel bg-sel text-white"
                    : "border-line-strong bg-surface text-ink-3",
                )}
              >
                {label}
              </span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

/** Khung cửa sổ chung cho các bước 1/3/4/5 — tiêu đề + chip "Bước x/5". */
export function StepWindow({
  step,
  children,
}: {
  step: number;
  children: ReactNode;
}) {
  return (
    <Card>
      <WindowBar title="Đặt lịch massage online">
        <span className="rounded-[3px] border border-line-strong bg-surface px-1.5 py-px text-[10px] text-ink-2">
          Bước {step}/5
        </span>
      </WindowBar>
      {children}
    </Card>
  );
}
