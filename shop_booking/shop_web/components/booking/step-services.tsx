"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import type { RequestState } from "@/lib/use-request";
import type { Course, Gender, ServicesResponse, Shop, Therapist } from "@/lib/types";
import {
  Alert,
  Button,
  Card,
  Chip,
  Field,
  LoadingLine,
  Note,
  WindowBar,
  cx,
} from "@/components/ui";
import {
  addMinutesToTime,
  formatDateShortVi,
  formatYen,
  parseIso,
  toIso,
  today,
} from "@/lib/format";
import { SlotLegend, SlotTimeline } from "./slot-timeline";
import type { PartySize } from "./booking-wizard";

const GENDER_OPTIONS: Array<{ label: string; value: Gender | null }> = [
  { label: "Không", value: null },
  { label: "NV nam", value: "male" },
  { label: "NV nữ", value: "female" },
];

/** Nhãn kiểu .chip.on của wireframe nhưng KHÔNG bấm được (chỉ là nhãn). */
function DarkLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-[3px] bg-sel px-2 py-0.5 text-xs text-white">
      {children}
    </span>
  );
}

/** Ô giá trị kiểu .box của wireframe. */
function ValueBox({
  children,
  filled,
  className,
}: {
  children: React.ReactNode;
  filled?: boolean;
  className?: string;
}) {
  return (
    <span
      className={cx(
        "rounded border border-line-strong px-2 py-0.5 text-sm tabular-nums",
        filled ? "bg-fill" : "bg-surface",
        className,
      )}
    >
      {children}
    </span>
  );
}

export function StepServices({
  shop,
  date,
  partySize,
  services,
  courseId,
  guestAddons,
  therapistGender,
  therapist,
  startTime,
  onSelectCourse,
  onChangeGuestAddons,
  onSelectTherapistGender,
  onSelectTherapist,
  onSelectStartTime,
  onSelectDate,
  onBack,
  onNext,
}: {
  shop: Shop;
  date: string;
  partySize: PartySize;
  services: RequestState<ServicesResponse>;
  courseId: number | null;
  guestAddons: number[][];
  therapistGender: Gender | null;
  therapist: Therapist | null;
  startTime: string | null;
  onSelectCourse: (id: number) => void;
  onChangeGuestAddons: (next: number[][]) => void;
  onSelectTherapistGender: (next: Gender | null) => void;
  onSelectTherapist: (next: Therapist | null) => void;
  onSelectStartTime: (time: string) => void;
  /** ◀ ▶ trên thanh timeline — đổi ngày ngay tại bước này. */
  onSelectDate: (iso: string) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const courses = useMemo(() => services.data?.courses ?? [], [services.data]);
  const addons = useMemo(() => services.data?.addons ?? [], [services.data]);
  const course = courses.find((item) => item.id === courseId) ?? null;

  // Hàng khách vừa bấm trên timeline — chỉ để vẽ ô "lượt của bạn" đúng hàng đó.
  const [pickedTherapistId, setPickedTherapistId] = useState<number | null>(null);

  /**
   * GET /slots chỉ nhận MỘT bộ addon_ids và áp cho mọi người, trong khi
   * POST /bookings nhận add-on riêng từng người. Gửi hợp của tất cả add-on là
   * phía an toàn: thời lượng ước tính ≥ thực tế, nên slot hiện ra không bao giờ
   * ngắn hơn nhu cầu (thà thiếu slot còn hơn gợi ý giờ rồi bị 409).
   */
  const addonUnion = useMemo(
    () => [...new Set(guestAddons.flat())].sort((a, b) => a - b),
    [guestAddons],
  );
  const addonUnionKey = addonUnion.join(",");

  const slots = useRequest(
    courseId
      ? `${shop.id}|${date}|${partySize}|${courseId}|${addonUnionKey}|${therapistGender ?? ""}|${therapist?.id ?? ""}`
      : null,
    // Chỉ chạy khi key khác null, tức courseId chắc chắn đã có.
    (signal) =>
      api.slots(
        shop.id,
        {
          date,
          partySize,
          courseId: courseId!,
          addonIds: addonUnion,
          therapistGender,
          therapistId: therapist?.id ?? null,
        },
        signal,
      ),
  );

  // Lịch theo từng nhân viên — nguồn dữ liệu của timeline (không phụ thuộc course).
  const timeline = useRequest(`tl|${shop.id}|${date}`, (signal) =>
    api.timeline(shop.id, date, signal),
  );

  // BR-04: chỉ hỏi danh sách nhân viên khi đi 1 người — nhóm ≥2 không được chỉ định.
  const therapists = useRequest(
    partySize === 1 ? `${shop.id}|${date}` : null,
    (signal) => api.therapists(shop.id, date, signal),
  );
  const therapistList = therapists.data?.therapists ?? [];

  const toggleAddon = (guestIndex: number, addonId: number) => {
    const next = guestAddons.map((list, index) => {
      if (index !== guestIndex) return list;
      return list.includes(addonId)
        ? list.filter((id) => id !== addonId)
        : [...list, addonId];
    });
    onChangeGuestAddons(next);
  };

  const guestDuration = (guestIndex: number) => {
    if (!course) return 0;
    const extra = guestAddons[guestIndex].reduce((sum, id) => {
      const addon = addons.find((item) => item.id === id);
      return sum + (addon?.duration_min ?? 0);
    }, 0);
    return course.duration_min + extra;
  };

  const totalPrice = useMemo(() => {
    if (!course) return 0;
    const addonTotal = guestAddons.flat().reduce((sum, id) => {
      const addon = addons.find((item) => item.id === id);
      return sum + (addon?.price ?? 0);
    }, 0);
    return course.price * partySize + addonTotal;
  }, [course, guestAddons, addons, partySize]);

  const maxDuration = course
    ? Math.max(...guestAddons.map((_, index) => guestDuration(index)))
    : 0;

  // Wireframe xếp course thành "tên bên trái + chips số phút": gom các course
  // trùng tên, mỗi thời lượng một chip.
  const courseGroups = useMemo(() => {
    const map = new Map<string, Course[]>();
    for (const item of courses) {
      map.set(item.name, [...(map.get(item.name) ?? []), item]);
    }
    return [...map.entries()].map(([name, list]) => ({
      name,
      list: [...list].sort((a, b) => a.duration_min - b.duration_min),
    }));
  }, [courses]);

  const slotList = slots.data?.slots ?? [];
  const noSlots =
    Boolean(courseId) && !slots.loading && !slots.error && slotList.length === 0;

  const isToday = date === toIso(today());
  const shiftDate = (delta: number) => {
    const next = parseIso(date);
    next.setDate(next.getDate() + delta);
    onSelectDate(toIso(next));
  };

  const timelineRows = timeline.data?.therapists ?? [];

  return (
    <div className="flex flex-col">
      {/* ------------------------------------------------ Cửa sổ 1: Lịch slot */}
      <Card>
        <WindowBar title="Lịch slot">
          <button
            type="button"
            onClick={() => shiftDate(-1)}
            disabled={isToday}
            aria-label="Ngày trước"
            className="rounded border border-line-strong bg-surface px-2 py-0.5 text-xs hover:bg-surface-2 disabled:pointer-events-none disabled:opacity-45"
          >
            ◀
          </button>
          <span className="rounded border border-line-strong bg-fill px-2 py-0.5 text-xs tabular-nums">
            {isToday ? "Hôm nay · " : ""}
            {formatDateShortVi(date)}
          </span>
          <button
            type="button"
            onClick={() => shiftDate(1)}
            aria-label="Ngày sau"
            className="rounded border border-line-strong bg-surface px-2 py-0.5 text-xs hover:bg-surface-2"
          >
            ▶
          </button>
          <span className="hidden rounded border border-line-strong bg-fill px-2 py-0.5 text-xs sm:inline">
            【{shop.shop_code}】{shop.name}
          </span>
        </WindowBar>

        <div className="flex flex-wrap items-center gap-2 border-b border-dashed border-line px-4 py-2">
          <SlotLegend />
        </div>

        <div className="px-0 py-0">
          {timeline.loading ? (
            <div className="px-4">
              <LoadingLine label="Đang tải lịch nhân viên…" />
            </div>
          ) : null}

          {timeline.error ? (
            <div className="px-4 py-3">
              <Alert tone="danger">
                {timeline.error.message}
                <div className="mt-2">
                  <Button onClick={timeline.reload} className="!py-0.5">
                    Thử lại
                  </Button>
                </div>
              </Alert>
            </div>
          ) : null}

          {/* Case A1 — ngày này không ai có ca */}
          {timeline.data && timelineRows.length === 0 ? (
            <div className="px-4 py-3">
              <Alert tone="warn">
                Cửa hàng không phục vụ ngày này, vui lòng chọn ngày khác.
              </Alert>
            </div>
          ) : null}

          {timelineRows.length > 0 ? (
            <SlotTimeline
              date={date}
              therapists={timelineRows}
              slots={slotList}
              hasCourse={Boolean(course) && !slots.loading}
              durationMin={maxDuration}
              courseLabel={course?.name ?? ""}
              selectedTime={startTime}
              selectedTherapistId={pickedTherapistId}
              requestedTherapistId={therapist?.id ?? null}
              requestedGender={therapistGender}
              onSelect={(time, therapistId) => {
                setPickedTherapistId(therapistId);
                onSelectStartTime(time);
              }}
            />
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 py-2">
          {slots.loading && course ? (
            <LoadingLine label="Đang tìm giờ trống…" />
          ) : null}

          {slots.error ? (
            <Alert tone="danger" className="flex-1">
              {slots.error.message}
              <div className="mt-2">
                <Button onClick={slots.reload} className="!py-0.5">
                  Thử lại
                </Button>
              </div>
            </Alert>
          ) : null}

          {/* Case A2 — ngày hết slot */}
          {noSlots ? (
            <Alert tone="warn" className="flex-1">
              Ngày này đã kín chỗ. Vui lòng chọn ngày khác bằng nút ◀ ▶ phía trên.
            </Alert>
          ) : null}

          {!course && timelineRows.length > 0 ? (
            <Note>Chọn course ở form bên dưới — slot trống nào cũng trắng, click là đặt</Note>
          ) : null}
        </div>
      </Card>

      {/* Mũi tên luồng như wireframe */}
      <div
        className="flex flex-col items-center gap-0.5 py-2 text-sm"
        style={{ color: "var(--now)" }}
        aria-hidden
      >
        <span>Click slot trống</span>
        <b className="text-xl leading-none">▼</b>
        <span>điền nốt form rồi Đăng ký</span>
      </div>

      {/* --------------------------------------------- Cửa sổ 2: Form đặt chỗ */}
      <Card>
        <WindowBar title="Form đặt chỗ">
          <button
            type="button"
            onClick={onBack}
            aria-label="Đóng — quay lại chọn cửa hàng"
            className="rounded-[3px] border border-line-strong bg-surface px-1.5 text-xs hover:bg-surface-2"
          >
            ✕
          </button>
        </WindowBar>

        {services.loading ? (
          <div className="px-4">
            <LoadingLine label="Đang tải dịch vụ…" />
          </div>
        ) : null}

        {services.error ? (
          <div className="px-4 py-3">
            <Alert tone="danger">{services.error.message}</Alert>
          </div>
        ) : null}

        {services.data ? (
          <>
            <Field label="Giờ bắt đầu">
              <div className="flex flex-wrap items-center gap-1.5">
                <DarkLabel>Ngày</DarkLabel>
                <ValueBox className="min-w-36">{formatDateShortVi(date)}</ValueBox>
                <DarkLabel>Giờ</DarkLabel>
                <ValueBox className="min-w-16">
                  {startTime
                    ? `${startTime} – ${addMinutesToTime(startTime, maxDuration)}`
                    : "—:—"}
                </ValueBox>
                {!startTime ? (
                  <Note>bấm một ô trống trên timeline phía trên</Note>
                ) : null}
              </div>
            </Field>

            <div className="flex flex-wrap items-center gap-2 border-b border-dashed border-line px-4 py-3">
              <span className="rounded border border-sel bg-sel px-2 py-0.5 text-xs text-white">
                Course đã chọn
              </span>
              <span className="text-xs text-ink-2">
                {course ? (
                  <>
                    {course.name}: {course.duration_min}p
                    {maxDuration > course.duration_min ? (
                      <> · Add-on: +{maxDuration - course.duration_min}p</>
                    ) : null}
                    {" · "}
                    <b>Tổng: {maxDuration}p</b> · {formatYen(totalPrice)}
                    {partySize > 1 ? ` (${partySize} người)` : ""}
                  </>
                ) : (
                  <>
                    Chưa chọn · <b>Tổng: 0p</b>
                  </>
                )}
              </span>
            </div>

            <Field
              label="Course"
              hint={partySize > 1 ? "Cả nhóm dùng chung một course" : undefined}
            >
              <div className="flex flex-col gap-2">
                {courseGroups.map((group, groupIndex) => (
                  <div
                    key={group.name}
                    className="flex flex-wrap items-center gap-1.5"
                  >
                    <span className="min-w-28 rounded border border-line-strong bg-fill px-2 py-0.5 text-center text-xs font-bold">
                      {group.name}
                    </span>
                    {group.list.map((item) => (
                      <Chip
                        key={item.id}
                        selected={item.id === courseId}
                        onClick={() => onSelectCourse(item.id)}
                        title={`${item.duration_min} phút · ${formatYen(item.price)}`}
                      >
                        {item.duration_min}
                      </Chip>
                    ))}
                    {groupIndex === 0 ? <Note>chips = số phút</Note> : null}
                  </div>
                ))}
              </div>
            </Field>

            <Field
              label="Tuỳ chọn"
              hint={partySize > 1 ? "Add-on chọn riêng từng người" : "Không bắt buộc"}
            >
              {!course ? (
                <p className="text-sm text-ink-3">
                  Chọn course trước để thêm tuỳ chọn.
                </p>
              ) : addons.length === 0 ? (
                <p className="text-sm text-ink-3">
                  Cửa hàng chưa có tuỳ chọn cho ngày này.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {guestAddons.map((selectedIds, guestIndex) => (
                    <div
                      key={guestIndex}
                      className="flex flex-wrap items-center gap-1.5"
                    >
                      {partySize > 1 ? (
                        <span className="text-xs text-ink-3">
                          Người {guestIndex + 1} · {guestDuration(guestIndex)}p
                        </span>
                      ) : null}
                      {addons.map((addon) => {
                        // BR-09: chặn sớm ở FE, BE vẫn kiểm lại khi tạo booking.
                        const restricted = addon.restricted_course_ids.includes(
                          course.id,
                        );
                        return (
                          <Chip
                            key={addon.id}
                            selected={selectedIds.includes(addon.id)}
                            disabled={restricted}
                            title={
                              restricted
                                ? `Không thể đặt kèm ${course.name}`
                                : `+${addon.duration_min}p · ${formatYen(addon.price)}`
                            }
                            onClick={() => toggleAddon(guestIndex, addon.id)}
                          >
                            {addon.name} +{addon.duration_min}p
                          </Chip>
                        );
                      })}
                    </div>
                  ))}
                </div>
              )}
            </Field>

            {/* BR-04: chỉ booking 1 người mới được chỉ định nhân viên. */}
            {partySize === 1 ? (
              <Field label="Chỉ định" hint="Không bắt buộc">
                <div className="flex flex-wrap items-center gap-1.5">
                  {GENDER_OPTIONS.map((option) => (
                    <Chip
                      key={option.label}
                      // "Không" chỉ sáng khi cũng không chọn đích danh ai.
                      selected={
                        therapist === null && therapistGender === option.value
                      }
                      onClick={() => onSelectTherapistGender(option.value)}
                    >
                      {option.label}
                    </Chip>
                  ))}
                  <span className="text-xs text-ink-3">· đích danh:</span>
                  {therapists.loading ? <LoadingLine label="Đang tải…" /> : null}
                  {therapistList.map((item) => {
                    const selected = therapist?.id === item.id;
                    return (
                      <Chip
                        key={item.id}
                        selected={selected}
                        tone="accent"
                        // Bấm lại người đang chọn = bỏ chỉ định.
                        onClick={() => onSelectTherapist(selected ? null : item)}
                      >
                        {item.name} ({item.gender === "male" ? "nam" : "nữ"})
                      </Chip>
                    );
                  })}
                  {/* Case A4 — không ai có ca ngày này để chỉ định đích danh */}
                  {!therapists.loading &&
                  !therapists.error &&
                  therapistList.length === 0 ? (
                    <span className="text-xs text-ink-3">
                      không có nhân viên nhận ca ngày này
                    </span>
                  ) : null}
                </div>
                {/* Không chặn luồng: chọn theo giới tính vẫn đặt được bình thường. */}
                {therapists.error ? (
                  <p className="mt-1.5 text-xs text-ink-3">
                    Không tải được danh sách nhân viên — bạn vẫn có thể chọn theo
                    giới tính.
                  </p>
                ) : null}
              </Field>
            ) : null}

            <div className="flex flex-wrap items-center justify-end gap-2 px-4 py-3">
              {partySize > 1 ? (
                <span className="mr-auto text-xs text-ink-3">
                  Nhóm {partySize} người: không chỉ định nhân viên (BR-04)
                </span>
              ) : null}
              <Button onClick={onBack}>Đóng</Button>
              <Button
                variant="primary"
                onClick={onNext}
                disabled={!courseId || !startTime}
              >
                Đăng ký ▶
              </Button>
            </div>
          </>
        ) : null}
      </Card>
    </div>
  );
}
