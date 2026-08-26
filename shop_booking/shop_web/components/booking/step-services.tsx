"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import type { RequestState } from "@/lib/use-request";
import type { Course, Gender, ServicesResponse, Shop, Therapist } from "@/lib/types";
import {
  Alert,
  Button,
  Chip,
  Field,
  LoadingLine,
  Note,
  cx,
} from "@/components/ui";
import {
  addMinutesToTime,
  formatDateShortVi,
  formatVnd,
  hhmmToMinutes,
  nowMinutes,
  parseIso,
  toIso,
  today,
} from "@/lib/format";
import { SlotLegend, SlotTimeline } from "./slot-timeline";
import { StepFooter } from "./step-footer";
import type { PartySize } from "./booking-wizard";

const GENDER_OPTIONS: Array<{ label: string; value: Gender }> = [
  { label: "NV nam", value: "male" },
  { label: "NV nữ", value: "female" },
];

/**
 * Dải tiêu đề mục bên trong MỘT cửa sổ. Wireframe 02 vẽ timeline và form thành
 * hai khung rời kèm mũi tên "click slot ▼ mở form" — đó là ký hiệu bản vẽ, ý đồ
 * ghi ngay ở tiêu đề: "timeline + form đặt chỗ 1 trang". Nên ở đây chỉ tách
 * bằng dải tiêu đề, không tách thành hai <Card>.
 */
function SectionBar({
  title,
  children,
  className,
}: {
  title: string;
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-2 border-b-[1.5px] border-frame bg-surface-2 px-3 py-1.5 text-xs text-ink-2",
        className,
      )}
    >
      <b className="text-ink">{title}</b>
      <span className="flex-1" />
      {children}
    </div>
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
  addonIds,
  therapistGender,
  therapist,
  noPreference,
  startTime,
  onSelectCourse,
  onChangeAddonIds,
  onSelectTherapistGender,
  onSelectNoPreference,
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
  addonIds: number[];
  therapistGender: Gender | null;
  therapist: Therapist | null;
  noPreference: boolean;
  startTime: string | null;
  onSelectCourse: (id: number) => void;
  onChangeAddonIds: (next: number[]) => void;
  onSelectTherapistGender: (next: Gender) => void;
  onSelectNoPreference: () => void;
  /** Chỉ định đích danh — gọi khi khách bấm slot của một nhân viên trên timeline. */
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

  // Cả nhóm dùng chung một bộ add-on (BR-10) nên gửi thẳng cho GET /slots — không còn
  // phải hợp nhất add-on của từng người rồi ước lượng thừa như trước.
  const addonKey = addonIds.join(",");

  // Đích danh giờ lọc phía client (rowFree trên timeline), nên KHÔNG gửi
  // therapist_id cho GET /slots — luôn lấy giờ nền của mọi nhân viên rồi timeline
  // tự lọc từng hàng. Chỉ giới tính mới nhờ BE lọc sẵn.
  const slots = useRequest(
    courseId
      ? `${shop.id}|${date}|${partySize}|${courseId}|${addonKey}|${therapistGender ?? ""}`
      : null,
    // Chỉ chạy khi key khác null, tức courseId chắc chắn đã có.
    (signal) =>
      api.slots(
        shop.id,
        {
          date,
          partySize,
          courseId: courseId!,
          addonIds,
          therapistGender,
          therapistId: null,
        },
        signal,
      ),
  );

  // Lịch theo từng nhân viên — nguồn dữ liệu của timeline (không phụ thuộc course).
  // Cũng là danh sách để khách chỉ định đích danh (bấm thẳng vào hàng nhân viên).
  const timeline = useRequest(`tl|${shop.id}|${date}`, (signal) =>
    api.timeline(shop.id, date, signal),
  );

  const toggleAddon = (addonId: number) => {
    onChangeAddonIds(
      addonIds.includes(addonId)
        ? addonIds.filter((id) => id !== addonId)
        : [...addonIds, addonId],
    );
  };

  const addonMinutes = useMemo(
    () =>
      addonIds.reduce((sum, id) => {
        const addon = addons.find((item) => item.id === id);
        return sum + (addon?.duration_min ?? 0);
      }, 0),
    [addonIds, addons],
  );

  const totalPrice = useMemo(() => {
    if (!course) return 0;
    const addonPrice = addonIds.reduce((sum, id) => {
      const addon = addons.find((item) => item.id === id);
      return sum + (addon?.price ?? 0);
    }, 0);
    // Add-on áp cho CẢ NHÓM nên phải nhân theo số người — trước đây guestAddons.flat()
    // tự có đủ bản sao, giờ chỉ còn một danh sách nên phải nhân tường minh.
    return (course.price + addonPrice) * partySize;
  }, [course, addonIds, addons, partySize]);

  // Mọi người cùng course + cùng add-on -> thời lượng như nhau, không cần Math.max.
  const maxDuration = course ? course.duration_min + addonMinutes : 0;

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

  const isToday = date === toIso(today());
  // Đặt cho HÔM NAY thì bỏ các giờ đã trôi qua — không cho chọn lùi về quá khứ.
  // (BE vẫn là chốt chặn; đây chỉ là chặn sớm cho UX — validate hai tầng.)
  const slotList = useMemo(() => {
    const raw = slots.data?.slots ?? [];
    if (!isToday) return raw;
    const cutoff = nowMinutes();
    return raw.filter((t) => hhmmToMinutes(t) > cutoff);
  }, [slots.data, isToday]);
  const noSlots =
    Boolean(courseId) && !slots.loading && !slots.error && slotList.length === 0;

  const shiftDate = (delta: number) => {
    const next = parseIso(date);
    next.setDate(next.getDate() + delta);
    onSelectDate(toIso(next));
  };

  const timelineRows = timeline.data?.therapists ?? [];

  // Timeline gộp "Giờ trống" khi: nhóm ≥2 (BR-04), bấm "Không chỉ định", hoặc
  // chọn giới tính. Mặc định (chưa chọn gì) hiện danh sách nhân viên để khách
  // chỉ định đích danh bằng cách bấm thẳng vào hàng.
  const aggregated = partySize >= 2 || noPreference || therapistGender !== null;

  return (
    <>
      {/* ---------------------------------------------- Mục 1: Chọn dịch vụ */}
      <SectionBar title="1 · Chọn dịch vụ" />

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
          <Field
            label="Course"
            hint={partySize > 1 ? "Cả nhóm dùng chung một course" : undefined}
          >
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              {courseGroups.map((group) => {
                // Nhãn tên tách riêng chỉ đáng khi một tên có nhiều mức thời
                // lượng (wireframe: "tên trái + chips số phút"). Shop đặt tên
                // sẵn kiểu "Massage body 60" thì nhãn + chip lặp số phút, gộp
                // luôn tên vào chip cho gọn.
                const grouped = group.list.length > 1;
                return (
                  <div
                    key={group.name}
                    className="flex flex-wrap items-center gap-1.5"
                  >
                    {grouped ? (
                      <span className="rounded border border-line-strong bg-fill px-2 py-0.5 text-center text-xs font-bold">
                        {group.name}
                      </span>
                    ) : null}
                    {group.list.map((item) => (
                      <Chip
                        key={item.id}
                        selected={item.id === courseId}
                        onClick={() => onSelectCourse(item.id)}
                      >
                        {grouped ? "" : `${group.name} · `}
                        {item.duration_min}p · {formatVnd(item.price)}
                      </Chip>
                    ))}
                  </div>
                );
              })}
            </div>
          </Field>

          <Field
            label="Tuỳ chọn"
            hint={
              partySize > 1 ? "Cả nhóm dùng chung add-on" : "Không bắt buộc"
            }
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
              <div className="flex flex-col gap-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  {addons.map((addon) => {
                    // BR-09: chặn sớm ở FE, BE vẫn kiểm lại khi tạo booking.
                    const restricted = addon.restricted_course_ids.includes(
                      course.id,
                    );
                    return (
                      <Chip
                        key={addon.id}
                        selected={addonIds.includes(addon.id)}
                        disabled={restricted}
                        title={
                          restricted
                            ? `Không thể đặt kèm ${course.name}`
                            : undefined
                        }
                        onClick={() => toggleAddon(addon.id)}
                      >
                        {addon.name} +{addon.duration_min}p ·{" "}
                        {formatVnd(addon.price)}
                      </Chip>
                    );
                  })}
                </div>
                {/* BR-10: add-on áp cho cả nhóm — nói rõ để khách không tưởng
                    đang chọn riêng cho một người. */}
                {partySize > 1 ? (
                  <span className="text-xs text-ink-3">
                    Áp dụng cho cả {partySize} người
                  </span>
                ) : null}
              </div>
            )}
          </Field>

          {/* BR-04: chỉ booking 1 người mới được chỉ định nhân viên. Đích danh
              KHÔNG còn chip ở đây — khách chỉ định bằng cách bấm thẳng slot của
              nhân viên trên timeline (mục 2). Ở đây chỉ còn "Không / nam / nữ",
              bấm là chuyển timeline sang Giờ trống gộp. */}
          {partySize === 1 ? (
            <Field label="Chỉ định" hint="Không bắt buộc">
              <div className="flex flex-wrap items-center gap-1.5">
                <Chip selected={noPreference} onClick={onSelectNoPreference}>
                  Không
                </Chip>
                {GENDER_OPTIONS.map((option) => (
                  <Chip
                    key={option.value}
                    selected={therapistGender === option.value}
                    onClick={() => onSelectTherapistGender(option.value)}
                  >
                    {option.label}
                  </Chip>
                ))}
                {therapist ? (
                  <span className="text-xs text-accent-hover">
                    · đích danh: <b>{therapist.name}</b> — bấm nhân viên khác trên
                    lịch để đổi
                  </span>
                ) : (
                  <span className="text-xs text-ink-3">
                    · hoặc bấm thẳng nhân viên trên lịch để chỉ định đích danh
                  </span>
                )}
              </div>
            </Field>
          ) : null}

          {/* Tổng kết dịch vụ — chốt mục 1 bằng dòng "tạm tính" trước khi sang
              chọn giờ. Không kẻ viền dưới vì thanh mục 2 ngay sau đã có viền. */}
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
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
                  <b>Tổng: {maxDuration}p</b> · {formatVnd(totalPrice)}
                  {partySize > 1 ? ` (${partySize} người)` : ""}
                </>
              ) : (
                <>
                  Chưa chọn · <b>Tổng: 0p</b>
                </>
              )}
            </span>
          </div>
        </>
      ) : null}

      {/* -------------------------------------------------- Mục 2: Chọn giờ */}
      <SectionBar title="2 · Chọn giờ" className="border-t-[1.5px]">
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
      </SectionBar>

      <div className="flex flex-wrap items-center gap-2 border-b border-dashed border-line px-4 py-2">
        {/* Khớp chế độ timeline: gộp "giờ trống" khi nhóm ≥2, "Không chỉ định",
            hoặc chọn giới tính. Mặc định (chưa chọn) hiện danh sách nhân viên. */}
        <SlotLegend aggregated={aggregated} />
      </div>

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
          partySize={partySize}
          aggregated={aggregated}
          hasCourse={Boolean(course) && !slots.loading}
          durationMin={maxDuration}
          courseLabel={course?.name ?? ""}
          selectedTime={startTime}
          selectedTherapistId={pickedTherapistId}
          requestedGender={therapistGender}
          onSelect={(time, therapistId) => {
            setPickedTherapistId(therapistId);
            // Chế độ danh sách: bấm slot của một nhân viên = chỉ định đích danh
            // người đó. Chế độ Giờ trống gộp trả therapistId = null (shop tự xếp).
            if (therapistId !== null) {
              const picked = timelineRows.find((t) => t.id === therapistId);
              if (picked)
                onSelectTherapist({
                  id: picked.id,
                  name: picked.name,
                  gender: picked.gender,
                });
            }
            onSelectStartTime(time);
          }}
        />
      ) : null}

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
          <Note>Chọn course ở mục 1 phía trên để hiện giờ trống</Note>
        ) : null}
      </div>

      {/* Ngày đã nằm trên thanh mục 2 nên chỉ hiện khoảng giờ, khỏi lặp lại. */}
      {services.data ? (
        <Field label="Giờ đã chọn">
          <div className="flex flex-wrap items-center gap-1.5">
            <ValueBox className="min-w-32" filled={Boolean(startTime)}>
              {startTime
                ? `${startTime} – ${addMinutesToTime(startTime, maxDuration)}`
                : "—:—"}
            </ValueBox>
            {!startTime ? (
              <Note>bấm một ô viền đứt trên lịch phía trên</Note>
            ) : null}
          </div>
        </Field>
      ) : null}

      {/* Hàng cuối chung cho cả trang. Để NGOÀI nhánh services.data để lúc dịch
          vụ đang tải hoặc lỗi khách vẫn quay lại được. */}
      <StepFooter
        onBack={onBack}
        onNext={onNext}
        nextLabel="Đăng ký"
        nextDisabled={!courseId || !startTime}
      >
        {partySize > 1 ? (
          <p className="text-xs text-ink-3">
            Nhóm {partySize} người: không chỉ định nhân viên (BR-04)
          </p>
        ) : null}
      </StepFooter>
    </>
  );
}
