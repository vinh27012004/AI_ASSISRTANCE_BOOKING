"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Gender, TimelineTherapist } from "@/lib/types";
import { minutesToTime, timeToMinutes, toIso, today } from "@/lib/format";

/** Kích thước lấy theo wireframe: hàng 30px, mỗi giờ ~48px (40px gốc hơi chật). */
const ROW_HEIGHT = 34;
/**
 * Bề rộng một giờ co giãn theo khung: timeline luôn kéo hết chiều ngang, chỉ khi
 * hẹp quá mới rơi về mức tối thiểu và cuộn ngang. Tối thiểu đủ rộng để bốn mốc
 * `09 :15 :30 :45` không dính nhau.
 */
const MIN_HOUR_WIDTH = 96;
/** Cột tên nhân viên (.tname). */
const NAME_WIDTH = 104;
/** Mốc phút trong mỗi giờ — chỉ để đọc trục, không phải giờ đặt được. */
const MINUTE_TICKS = [0, 15, 30, 45] as const;

/** Đo bề ngang khung cuộn để tính bề rộng mỗi giờ. */
function useElementWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    // Khung cuộn rộng theo cha, KHÔNG theo nội dung — đo rồi đổi nội dung bên
    // trong không làm nó co lại, nên không có vòng lặp observer.
    const observer = new ResizeObserver(([entry]) => {
      setWidth(entry.contentRect.width);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return [ref, width] as const;
}

/** Sọc chéo "ngoài ca / khoá" — đúng mã màu wireframe (.seg gạch #d8d4c8 trên #eceae4). */
const HATCH =
  "repeating-linear-gradient(45deg,transparent 0 5px,var(--tl-hatch) 5px 7px)";

/** Khoảng cách giữa hai giờ liền nhau — BE có thể đổi bước 30p/15p tuỳ shop. */
function slotStep(minutes: number[]): number {
  let step = 30;
  for (let i = 1; i < minutes.length; i += 1) {
    step = Math.min(step, minutes[i] - minutes[i - 1]);
  }
  return Math.max(step, 5);
}

/** Phút hiện tại, chỉ khi `date` đúng là hôm nay — dùng vẽ vạch "bây giờ". */
function useNowMinutes(date: string): number | null {
  const [now, setNow] = useState<number | null>(null);

  useEffect(() => {
    const tick = () => {
      if (date !== toIso(today())) {
        setNow(null);
        return;
      }
      const d = new Date();
      setNow(d.getHours() * 60 + d.getMinutes());
    };
    tick();
    const id = setInterval(tick, 60_000);
    return () => clearInterval(id);
  }, [date]);

  return now;
}

type Range = { start: number; end: number };

function toRanges(list: Array<{ start_time: string; end_time: string }>): Range[] {
  return list.map((item) => ({
    start: timeToMinutes(item.start_time),
    end: timeToMinutes(item.end_time),
  }));
}

/** Gộp các khoảng chồng lấn thành dải liền mạch, trả về đã sắp xếp. */
function mergeRanges(list: Range[]): Range[] {
  const sorted = [...list].sort((a, b) => a.start - b.start);
  const out: Range[] = [];
  for (const r of sorted) {
    const last = out[out.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else out.push({ ...r });
  }
  return out;
}

/** Phần bù của `ranges` (đã sắp xếp) trong [from, to) — chỗ vẽ sọc chéo. */
function gapsIn(ranges: Range[], from: number, to: number): Range[] {
  const out: Range[] = [];
  let cursor = from;
  for (const r of ranges) {
    if (r.start > cursor) out.push({ start: cursor, end: r.start });
    cursor = Math.max(cursor, r.end);
  }
  if (cursor < to) out.push({ start: cursor, end: to });
  return out;
}

/** Nhân viên có phục vụ được lượt [start, start+dur) không: ca phủ kín + không đè lịch đã đặt. */
function rowFree(shifts: Range[], busy: Range[], start: number, dur: number): boolean {
  const covered = shifts.some((s) => s.start <= start && s.end >= start + dur);
  if (!covered) return false;
  return !busy.some((b) => Math.max(start, b.start) < Math.min(start + dur, b.end));
}

/**
 * Timeline kiểu wireframe 02. Hai chế độ:
 *
 * - **1 người** — mỗi nhân viên một hàng có tên: khách còn được chỉ định đích
 *   danh nên cần biết ai rảnh giờ nào.
 * - **Nhóm ≥2 người** — GỘP thành một hàng duy nhất, không tên. BR-04 cấm chỉ
 *   định nhân viên cho nhóm, BE tự phân người lúc tạo booking, nên bày tên ra
 *   chỉ khiến khách tưởng mình chọn được. Cái khách cần biết là "khung giờ nào
 *   nhận nổi cả N người" — đúng bằng `slots`, vì `GET /slots` đã tính sẵn theo
 *   `party_size`.
 */
export function SlotTimeline({
  date,
  therapists,
  slots,
  partySize,
  hasCourse,
  durationMin,
  courseLabel,
  selectedTime,
  selectedTherapistId,
  requestedTherapistId,
  requestedGender,
  onSelect,
}: {
  date: string;
  therapists: TimelineTherapist[];
  /** Giờ bắt đầu hợp lệ từ GET /slots — đã lọc theo course/số người/chỉ định. */
  slots: string[];
  partySize: number;
  hasCourse: boolean;
  durationMin: number;
  courseLabel: string;
  selectedTime: string | null;
  /** Hàng khách đã bấm — chỉ để vẽ ô "lượt của bạn" đúng hàng. */
  selectedTherapistId: number | null;
  requestedTherapistId: number | null;
  requestedGender: Gender | null;
  /** `therapistId` là null ở chế độ nhóm — lúc đó không có hàng nào để ghim. */
  onSelect: (time: string, therapistId: number | null) => void;
}) {
  const groupMode = partySize >= 2;
  const nowMinutes = useNowMinutes(date);
  const [scrollRef, frameWidth] = useElementWidth<HTMLDivElement>();

  const rows = useMemo(
    () =>
      therapists.map((t) => ({
        ...t,
        shiftRanges: toRanges(t.shifts),
        busyRanges: toRanges(t.bookings),
      })),
    [therapists],
  );

  const slotMinutes = useMemo(
    () => slots.map(timeToMinutes).sort((a, b) => a - b),
    [slots],
  );

  // Chế độ nhóm không vẽ ca từng người nữa, nhưng vẫn phải cho thấy khung giờ
  // cửa hàng có người trực — hợp của mọi ca trong ngày.
  const serviceRanges = useMemo(
    () => mergeRanges(rows.flatMap((r) => r.shiftRanges)),
    [rows],
  );

  const axis = useMemo(() => {
    // Trục phải ôm hết: ca làm, lịch đã đặt, và lượt dài nhất đặt từ slot cuối.
    const points: number[] = [];
    for (const row of rows) {
      for (const r of row.shiftRanges) points.push(r.start, r.end);
      for (const r of row.busyRanges) points.push(r.start, r.end);
    }
    if (slotMinutes.length > 0) {
      points.push(slotMinutes[0]);
      points.push(slotMinutes[slotMinutes.length - 1] + durationMin);
    }
    const from = points.length ? Math.floor(Math.min(...points) / 60) * 60 : 9 * 60;
    const to = points.length
      ? Math.max(Math.ceil(Math.max(...points) / 60) * 60, from + 60)
      : 21 * 60;

    // Chia đều phần còn lại của khung cho số giờ — timeline trải hết bề ngang.
    // Khung chưa đo xong (frameWidth = 0) hoặc quá hẹp thì dùng mức tối thiểu.
    const hourCount = (to - from) / 60;
    const free = frameWidth - NAME_WIDTH;
    const hourWidth = Math.max(MIN_HOUR_WIDTH, free > 0 ? free / hourCount : 0);

    return {
      from,
      to,
      hourWidth,
      pxPerMin: hourWidth / 60,
      width: hourCount * hourWidth,
      hours: Array.from({ length: hourCount }, (_, i) => from / 60 + i),
      step: slotStep(slotMinutes),
    };
  }, [rows, slotMinutes, durationMin, frameWidth]);

  const selectedStart = selectedTime ? timeToMinutes(selectedTime) : null;

  /** Hàng được phép bấm theo chỉ định hiện tại (đích danh / giới tính). */
  const clickable = useCallback(
    (row: { id: number; gender: Gender }) => {
      if (requestedTherapistId !== null) return row.id === requestedTherapistId;
      if (requestedGender !== null) return row.gender === requestedGender;
      return true;
    },
    [requestedTherapistId, requestedGender],
  );

  // Chọn giờ từ nơi khác (gợi ý 409) thì chưa biết hàng — vẽ lên hàng trống đầu tiên.
  const selectedRowId = useMemo(() => {
    if (selectedStart === null) return null;
    if (
      selectedTherapistId !== null &&
      rows.some(
        (r) =>
          r.id === selectedTherapistId &&
          rowFree(r.shiftRanges, r.busyRanges, selectedStart, durationMin),
      )
    ) {
      return selectedTherapistId;
    }
    return (
      rows.find(
        (r) =>
          clickable(r) &&
          rowFree(r.shiftRanges, r.busyRanges, selectedStart, durationMin),
      )?.id ?? null
    );
  }, [rows, selectedStart, selectedTherapistId, durationMin, clickable]);

  if (rows.length === 0) return null;

  const left = (min: number) => (min - axis.from) * axis.pxPerMin;

  return (
    <div ref={scrollRef} className="overflow-x-auto">
      <div style={{ width: NAME_WIDTH + axis.width, minWidth: "100%" }}>
        {/* Nhãn trục (.hh) tách hai hàng: số giờ ở trên, mốc phút ở dưới. */}
        <div aria-hidden>
          <div className="flex">
            <span
              className="sticky left-0 z-20 shrink-0"
              style={{ width: NAME_WIDTH, background: "var(--tl-corner)" }}
            />
            {axis.hours.map((hour) => (
              <span
                key={hour}
                className="shrink-0 border-r border-line-strong py-0.5 text-center text-[11px] font-bold text-ink tabular-nums"
                style={{ width: axis.hourWidth, background: "var(--tl-head)" }}
              >
                {String(hour).padStart(2, "0")}
              </span>
            ))}
          </div>

          <div className="flex">
            <span
              className="sticky left-0 z-20 shrink-0"
              style={{ width: NAME_WIDTH, background: "var(--tl-corner)" }}
            />
            {axis.hours.map((hour) => (
              <span
                key={hour}
                className="flex shrink-0 border-r border-line-strong"
                style={{ width: axis.hourWidth, background: "var(--tl-head)" }}
              >
                {MINUTE_TICKS.map((minute) => (
                  <span
                    key={minute}
                    // Vạch đầu mỗi giờ đã có border-r của ô giờ trước lo, bỏ đi
                    // cho khỏi thành hai nét chồng nhau.
                    className="flex-1 border-l border-dashed border-line py-px text-center text-[9px] text-ink-3 tabular-nums first:border-l-0"
                  >
                    {String(minute).padStart(2, "0")}
                  </span>
                ))}
              </span>
            ))}
          </div>
        </div>

        <div className="relative">
          {groupMode ? (
            <div className="flex">
              <span
                className="sticky left-0 z-10 shrink-0 border-r border-line-strong px-1.5 py-0.5 text-[11px] leading-tight text-ink"
                style={{
                  width: NAME_WIDTH,
                  height: ROW_HEIGHT,
                  background: "var(--tl-corner)",
                }}
              >
                ◯ Cả nhóm
                <br />
                <span className="text-[9.5px] text-ink-3">
                  {partySize} người · shop tự xếp
                </span>
              </span>

              <div
                className="relative"
                style={{
                  width: axis.width,
                  height: ROW_HEIGHT,
                  backgroundImage: [
                    `repeating-linear-gradient(90deg,var(--tl-row-line) 0 1px,transparent 1px ${axis.hourWidth}px)`,
                    `repeating-linear-gradient(90deg,var(--tl-grid) 0 1px,transparent 1px ${axis.hourWidth / 4}px)`,
                  ].join(","),
                  backgroundColor: "var(--color-surface)",
                  borderBottom: "1px solid var(--tl-row-line)",
                }}
              >
                {/* ▨ ngoài giờ phục vụ — không ai trực, khác hẳn "kín chỗ" */}
                {gapsIn(serviceRanges, axis.from, axis.to).map((r) => (
                  <span
                    key={`off-${r.start}`}
                    aria-hidden
                    className="absolute inset-y-0"
                    style={{
                      left: left(r.start),
                      width: (r.end - r.start) * axis.pxPerMin,
                      background: "var(--fill)",
                      backgroundImage: HATCH,
                    }}
                  />
                ))}

                {/* Slot đã do BE lọc theo party_size — có mặt ở đây nghĩa là đủ
                    người phục vụ cả nhóm cùng lúc. Chỗ trống không có ô nào là
                    kín chỗ cho nhóm này. */}
                {hasCourse
                  ? slotMinutes.map((t) => (
                      <button
                        key={t}
                        type="button"
                        onClick={() => onSelect(minutesToTime(t), null)}
                        aria-pressed={selectedStart === t}
                        aria-label={`${minutesToTime(t)} — nhận được ${partySize} người`}
                        title={`${minutesToTime(t)} – ${minutesToTime(t + durationMin)} · nhận được ${partySize} người`}
                        className="absolute inset-y-0.5 z-10 rounded-[3px] border border-dashed border-line-strong transition-colors hover:border-solid hover:border-accent hover:bg-accent-soft"
                        style={{
                          left: left(t),
                          width: axis.step * axis.pxPerMin - 2,
                        }}
                      />
                    ))
                  : null}

                {selectedStart !== null ? (
                  <span
                    className="pointer-events-none absolute inset-y-0 z-30 overflow-hidden rounded-[3px] border border-dashed border-white bg-accent px-1 py-0.5 text-[8.5px] leading-tight text-white"
                    style={{
                      left: left(selectedStart),
                      width: durationMin * axis.pxPerMin,
                    }}
                  >
                    {courseLabel} {durationMin}p × {partySize}
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}

          {groupMode ? null : rows.map((row) => {
            const rowClickable = clickable(row);
            // Khoảng NGOÀI CA = phần bù của các ca trong trục — vẽ gạch chéo.
            const offShift = gapsIn(
              mergeRanges(row.shiftRanges),
              axis.from,
              axis.to,
            );

            return (
              <div key={row.id} className="flex">
                {/* Cột tên (.tname) — sticky để cuộn ngang vẫn thấy tên */}
                <span
                  className="sticky left-0 z-10 shrink-0 border-r border-line-strong px-1.5 py-0.5 text-[11px] leading-tight text-ink"
                  style={{
                    width: NAME_WIDTH,
                    height: ROW_HEIGHT,
                    background: "var(--tl-corner)",
                  }}
                >
                  ◯ {row.name}
                  <br />
                  <span className="text-[9.5px] text-ink-3">
                    {row.shifts
                      .map((s) => `ca ${s.start_time}–${s.end_time}`)
                      .join(" · ")}
                  </span>
                </span>

                <div
                  className="relative"
                  style={{
                    width: axis.width,
                    height: ROW_HEIGHT,
                    // Kẻ đậm mỗi đầu giờ, kẻ mờ mỗi 15 phút — khớp mốc ở hàng giờ.
                    backgroundImage: [
                      `repeating-linear-gradient(90deg,var(--tl-row-line) 0 1px,transparent 1px ${axis.hourWidth}px)`,
                      `repeating-linear-gradient(90deg,var(--tl-grid) 0 1px,transparent 1px ${axis.hourWidth / 4}px)`,
                    ].join(","),
                    backgroundColor: "var(--color-surface)",
                    borderBottom: "1px solid var(--tl-row-line)",
                  }}
                >
                  {/* ▨ ngoài ca */}
                  {offShift.map((r) => (
                    <span
                      key={`off-${r.start}`}
                      aria-hidden
                      className="absolute inset-y-0"
                      style={{
                        left: left(r.start),
                        width: (r.end - r.start) * axis.pxPerMin,
                        background: "var(--fill)",
                        backgroundImage: HATCH,
                      }}
                    />
                  ))}

           
                  {hasCourse && rowClickable
                    ? slotMinutes
                        .filter((t) =>
                          rowFree(row.shiftRanges, row.busyRanges, t, durationMin),
                        )
                        .map((t) => (
                          <button
                            key={t}
                            type="button"
                            onClick={() => onSelect(minutesToTime(t), row.id)}
                            aria-pressed={
                              selectedStart === t && selectedRowId === row.id
                            }
                            aria-label={`${minutesToTime(t)} — ${row.name}`}
                            title={`${minutesToTime(t)} – ${minutesToTime(t + durationMin)} · ${row.name}`}
                            className="absolute inset-y-0.5 z-10 rounded-[3px] border border-dashed border-line-strong transition-colors hover:border-solid hover:border-accent hover:bg-accent-soft"
                            style={{
                              left: left(t),
                              width: axis.step * axis.pxPerMin - 2,
                            }}
                          />
                        ))
                    : null}

        
                  {row.busyRanges.map((r, i) => (
                    <span
                      key={`busy-${r.start}`}
                      className="absolute inset-y-0 z-20 overflow-hidden rounded-[3px] border border-accent bg-accent-soft px-1 py-0.5 text-[8.5px] leading-tight text-accent-hover"
                      style={{ left: left(r.start), width: (r.end - r.start) * axis.pxPerMin }}
                      title={`${row.bookings[i].course_name} · ${row.bookings[i].start_time}–${row.bookings[i].end_time}`}
                    >
                      {row.bookings[i].course_name} {r.end - r.start}p
                    </span>
                  ))}

                  {/* Lượt bạn đang chọn — xanh nhưng viền đứt trắng để khác "đã đặt" */}
                  {selectedStart !== null && selectedRowId === row.id ? (
                    <span
                      className="pointer-events-none absolute inset-y-0 z-30 overflow-hidden rounded-[3px] border border-dashed border-white bg-accent px-1 py-0.5 text-[8.5px] leading-tight text-white"
                      style={{
                        left: left(selectedStart),
                        width: durationMin * axis.pxPerMin,
                      }}
                    >
                      {courseLabel} {durationMin}p
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })}

          {/* Vạch đỏ đứt "bây giờ" */}
          {nowMinutes !== null &&
          nowMinutes >= axis.from &&
          nowMinutes <= axis.to ? (
            <div
              aria-hidden
              className="pointer-events-none absolute inset-y-0 z-30 border-l-[1.5px] border-dashed"
              style={{
                left: NAME_WIDTH + left(nowMinutes),
                borderColor: "var(--now)",
              }}
            />
          ) : null}
        </div>
      </div>
    </div>
  );
}

/**
 * Chú giải — luôn kèm chữ, không bắt khách nhớ màu (đúng wireframe). Phải đi
 * đúng bộ với chế độ của timeline: chế độ nhóm không vẽ block "đã đặt" của
 * người khác nên không được liệt kê nó ra.
 */
export function SlotLegend({ groupMode }: { groupMode?: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs text-ink-3">
      <span className="shrink-0">Chú giải:</span>
      <span className="rounded-[3px] border border-dashed border-line-strong bg-surface px-2 py-0.5 text-ink-2">
        Trống — đặt được
      </span>
      {groupMode ? (
        <span className="rounded-[3px] border border-line-strong bg-surface px-2 py-0.5 text-ink-3">
          Để trắng — kín chỗ cho nhóm
        </span>
      ) : (
        <span className="rounded-[3px] border border-accent bg-accent-soft px-2 py-0.5 text-accent-hover">
          ■ Đã đặt (kèm tên course)
        </span>
      )}
      <span
        className="rounded-[3px] border border-line-strong px-2 py-0.5 text-ink-2"
        style={{ background: "var(--fill)" }}
      >
        ▨ {groupMode ? "Ngoài giờ phục vụ" : "Ngoài ca / khoá"}
      </span>
      <span className="rounded-[3px] border border-dashed border-white bg-accent px-2 py-0.5 text-white">
        ◼ Lượt bạn đang chọn
      </span>
    </div>
  );
}
