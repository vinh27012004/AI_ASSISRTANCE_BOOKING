import type { ReactNode } from "react";
import { Button } from "@/components/ui";

/** Hàng cuối cửa sổ như wireframe: trái "◀ Quay lại", phải nút tối "… ▶". */
export function StepFooter({
  onBack,
  onNext,
  nextLabel = "Tiếp tục",
  nextDisabled,
  children,
}: {
  onBack?: () => void;
  onNext?: () => void;
  nextLabel?: string;
  nextDisabled?: boolean;
  /** Nội dung phụ ở giữa (ghi chú, tổng tiền…). */
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
      {onBack ? (
        <Button onClick={onBack}>◀ Quay lại</Button>
      ) : (
        <span />
      )}

      {children ? (
        <div className="order-last w-full sm:order-none sm:w-auto">{children}</div>
      ) : null}

      {onNext ? (
        <Button variant="primary" onClick={onNext} disabled={nextDisabled}>
          {nextLabel} ▶
        </Button>
      ) : (
        <span />
      )}
    </div>
  );
}
