"use client";

import { useMemo, useState } from "react";
import { ApiError, api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import type { Booking } from "@/lib/types";
import { Alert, Button, Chip, Field, LoadingLine, Spinner } from "@/components/ui";
import { Calendar } from "./calendar";

/**
 * Đổi ngày/giờ cho một booking đã có. Chỉ đụng tới `date` + `start_time` —
 * PATCH là merge nên course/add-on/chỉ định nhân viên giữ nguyên, khách muốn
 * đổi dịch vụ thì huỷ rồi đặt lại (US-02).
 */
export function ReschedulePanel({
  booking,
  /** Có edit_token thì khỏi cần email (cửa sổ nhanh 2 phút — BR-17). */
  editToken,
  email,
  onDone,
  onCancel,
}: {
  booking: Booking;
  editToken?: string | null;
  email?: string;
  onDone: (next: Booking) => void;
  onCancel: () => void;
}) {
  const [date, setDate] = useState(booking.date);
  const [startTime, setStartTime] = useState<string | null>(null);
  const [closedDates, setClosedDates] = useState<ReadonlySet<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const courseId = booking.course?.id ?? null;

  /**
   * GET /slots nhận MỘT bộ addon_ids áp cho mọi người, còn booking giữ add-on
   * riêng từng người → gửi hợp để thời lượng ước tính ≥ thực tế, thà thiếu slot
   * còn hơn gợi ý giờ rồi ăn 409 (giống bước 2 của wizard).
   */
  const addonUnion = useMemo(
    () =>
      [
        ...new Set(
          booking.reservations.flatMap((reservation) =>
            reservation.addons.map((addon) => addon.id),
          ),
        ),
      ].sort((a, b) => a - b),
    [booking.reservations],
  );

  // Ngày shop nghỉ (A1) phát hiện qua services → gạch sẵn trên lịch.
  const services = useRequest(
    `${booking.shop.id}|${date}|${booking.party_size}`,
    (signal) =>
      api.services(
        booking.shop.id,
        { date, partySize: booking.party_size },
        signal,
      ),
  );

  const closedDate = services.data?.reason === "SHOP_CLOSED" ? date : null;
  if (closedDate && !closedDates.has(closedDate)) {
    setClosedDates(new Set(closedDates).add(closedDate));
  }

  const slots = useRequest(
    courseId && !closedDate
      ? `${booking.shop.id}|${date}|${courseId}|${addonUnion.join(",")}`
      : null,
    (signal) =>
      api.slots(
        booking.shop.id,
        {
          date,
          partySize: booking.party_size,
          courseId: courseId!,
          addonIds: addonUnion,
          // Giữ nguyên CHỈ ĐỊNH cũ để giờ hiện ra khớp với ràng buộc BE sẽ áp
          // khi PATCH — không gửi lại hai field này, BE tự merge.
          therapistGender: booking.requested_therapist_gender,
          therapistId: booking.requested_therapist_id,
        },
        signal,
      ),
  );

  const slotList = slots.data?.slots ?? [];
  const noSlots =
    Boolean(courseId) &&
    !closedDate &&
    !slots.loading &&
    !slots.error &&
    slotList.length === 0;

  const unchanged = date === booking.date && startTime === booking.start_time;

  const submit = async () => {
    if (!startTime || saving) return;
    setSaving(true);
    setError(null);
    try {
      onDone(
        await api.updateBooking(
          booking.booking_code,
          { date, start_time: startTime, ...(editToken ? {} : { email }) },
          editToken,
        ),
      );
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setSaving(false);
    }
  };

  // Case A6 — giờ vừa bị chiếm, BE gợi ý các giờ gần nhất.
  const suggestedSlots =
    error?.code === "SLOT_CONFLICT" && Array.isArray(error.details?.suggested_slots)
      ? (error.details.suggested_slots as string[])
      : [];

  return (
    <>
      <Field label="Ngày mới">
        <Calendar
          value={date}
          onChange={(next) => {
            setDate(next);
            setStartTime(null);
            setError(null);
          }}
          closedDates={closedDates}
        />

        {services.loading ? <LoadingLine label="Đang kiểm tra ngày…" /> : null}

        {/* Case A1 — shop nghỉ ngày đã chọn */}
        {closedDate ? (
          <Alert tone="warn" className="mt-2">
            Cửa hàng không phục vụ ngày này, vui lòng chọn ngày khác.
          </Alert>
        ) : null}

        {services.error ? (
          <Alert tone="danger" className="mt-2">
            {services.error.message}
          </Alert>
        ) : null}
      </Field>

      <Field label="Giờ mới" last>
        {!courseId ? (
          <p className="text-sm text-ink-3">
            Đặt chỗ này không còn course hợp lệ — vui lòng liên hệ cửa hàng ☎{" "}
            {booking.shop.phone}.
          </p>
        ) : (
          <>
            {slots.loading ? <LoadingLine label="Đang tìm giờ trống…" /> : null}

            {slots.error ? (
              <Alert tone="danger">
                {slots.error.message}
                <div className="mt-2">
                  <Button onClick={slots.reload} className="!py-1.5">
                    Thử lại
                  </Button>
                </div>
              </Alert>
            ) : null}

            {/* Case A2 — ngày kín chỗ */}
            {noSlots ? (
              <Alert tone="warn">
                Ngày này đã kín chỗ. Vui lòng chọn ngày khác.
              </Alert>
            ) : null}

            {slotList.length > 0 ? (
              <div className="grid grid-cols-4 gap-2 sm:grid-cols-6">
                {slotList.map((time) => (
                  <Chip
                    key={time}
                    selected={time === startTime}
                    onClick={() => {
                      setStartTime(time);
                      setError(null);
                    }}
                    className="tabular-nums"
                  >
                    {time}
                  </Chip>
                ))}
              </div>
            ) : null}

            {error ? (
              <Alert
                tone={error.code === "SLOT_CONFLICT" ? "warn" : "danger"}
                className="mt-3"
              >
                {error.message}

                {suggestedSlots.length > 0 ? (
                  <div className="mt-2.5 flex flex-wrap items-center gap-2">
                    <span className="text-xs">Gợi ý:</span>
                    {suggestedSlots.map((time) => (
                      <Chip
                        key={time}
                        onClick={() => {
                          setStartTime(time);
                          setError(null);
                        }}
                        className="tabular-nums"
                      >
                        {time}
                      </Chip>
                    ))}
                  </div>
                ) : null}
              </Alert>
            ) : null}
          </>
        )}
      </Field>

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-dashed border-line px-4 py-3">
        <Button onClick={onCancel} disabled={saving}>
          ◀ Quay lại
        </Button>
        <Button
          variant="primary"
          onClick={submit}
          disabled={!startTime || unchanged || saving}
          title={unchanged ? "Giờ mới trùng giờ hiện tại" : undefined}
          className="px-6"
        >
          {saving ? <Spinner /> : null}
          Lưu thay đổi
        </Button>
      </div>
    </>
  );
}
