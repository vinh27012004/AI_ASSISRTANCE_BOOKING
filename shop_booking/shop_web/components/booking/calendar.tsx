"use client";

import { useState } from "react";
import { cx } from "@/components/ui";
import {
  WEEKDAY_HEADERS,
  mondayIndex,
  monthLabel,
  parseIso,
  toIso,
  today,
} from "@/lib/format";

/** Cho đặt trước tối đa 3 tháng. */
const MONTHS_AHEAD = 3;

function startOfMonth(date: Date) {
  return new Date(date.getFullYear(), date.getMonth(), 1);
}

function addMonths(date: Date, delta: number) {
  return new Date(date.getFullYear(), date.getMonth() + delta, 1);
}

export function Calendar({
  value,
  onChange,
  closedDates,
}: {
  value: string | null;
  onChange: (iso: string) => void;
  /** Ngày đã biết là shop nghỉ (phát hiện qua reason=SHOP_CLOSED — A1). */
  closedDates: ReadonlySet<string>;
}) {
  const firstSelectable = startOfMonth(today());
  const lastSelectable = addMonths(firstSelectable, MONTHS_AHEAD);
  const [month, setMonth] = useState(() =>
    startOfMonth(value ? parseIso(value) : today()),
  );

  const canGoBack = month > firstSelectable;
  const canGoForward = month < lastSelectable;

  const daysInMonth = new Date(
    month.getFullYear(),
    month.getMonth() + 1,
    0,
  ).getDate();
  const leadingBlanks = mondayIndex(month);
  const todayIso = toIso(today());

  return (
    <div className="inline-block">
      {/* ◀ Tháng 7/2026 ▶ — dãy .box như wireframe 01 */}
      <div className="mb-2 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setMonth((m) => addMonths(m, -1))}
          disabled={!canGoBack}
          aria-label="Tháng trước"
          className="rounded border border-line-strong bg-surface px-2 py-0.5 text-xs text-ink-2 transition-colors hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-40"
        >
          ◀
        </button>
        <div className="rounded border border-line-strong bg-fill px-3 py-0.5 text-center text-xs">
          {monthLabel(month)}
        </div>
        <button
          type="button"
          onClick={() => setMonth((m) => addMonths(m, 1))}
          disabled={!canGoForward}
          aria-label="Tháng sau"
          className="rounded border border-line-strong bg-surface px-2 py-0.5 text-xs text-ink-2 transition-colors hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-40"
        >
          ▶
        </button>
      </div>

      <div className="grid grid-cols-7 gap-1">
        {WEEKDAY_HEADERS.map((day) => (
          <div
            key={day}
            className="pb-0.5 text-center text-[10px] text-ink-3"
          >
            {day}
          </div>
        ))}

        {Array.from({ length: leadingBlanks }, (_, i) => (
          <div key={`blank-${i}`} />
        ))}

        {Array.from({ length: daysInMonth }, (_, i) => {
          const day = i + 1;
          const iso = toIso(new Date(month.getFullYear(), month.getMonth(), day));
          const isPast = iso < todayIso;
          const isClosed = closedDates.has(iso);
          const disabled = isPast || isClosed;
          const selected = iso === value;

          return (
            <button
              key={iso}
              type="button"
              disabled={disabled}
              onClick={() => onChange(iso)}
              aria-label={iso}
              title={isClosed ? "Cửa hàng không phục vụ ngày này" : undefined}
              className={cx(
                // Ô ngày = .chip của wireframe: viền mảnh bo 3px.
                "flex size-8 items-center justify-center rounded-[3px] border text-xs tabular-nums transition-colors",
                // Ngày đang chọn luôn giữ nền tối, kể cả khi phát hiện shop
                // nghỉ — để cảnh báo A1 bên dưới có chỗ neo vào.
                selected && "border-sel bg-sel text-white",
                selected && disabled && "line-through opacity-60",
                !selected &&
                  !disabled &&
                  "border-line-strong bg-surface text-ink-2 hover:bg-surface-2 hover:text-ink",
                !selected &&
                  !disabled &&
                  iso === todayIso &&
                  "border-accent text-accent",
                // Mờ + viền đứt = quá khứ hoặc shop nghỉ (wireframe .chip.dis).
                !selected &&
                  disabled &&
                  "cursor-not-allowed border-dashed border-line bg-surface text-ink-3 opacity-50",
              )}
            >
              {day}
            </button>
          );
        })}
      </div>

      <p className="mt-2 text-xs text-ink-3">
        Ô mờ viền đứt = ngày đã qua hoặc cửa hàng không phục vụ.
      </p>
    </div>
  );
}
