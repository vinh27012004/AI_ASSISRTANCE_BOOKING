import type { ReactNode } from "react";
import type { Booking } from "@/lib/types";
import { addMinutesToTime, formatDateVi, formatYen } from "@/lib/format";
import { BookingStatusBadge } from "./booking-status-badge";

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 border-b border-line py-2.5 text-sm last:border-b-0">
      <div className="w-28 shrink-0 text-ink-3 sm:w-32">{label}</div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

/** Tổng tiền + thời lượng suy từ booking BE trả — BE không trả sẵn hai số này. */
export function bookingTotals(booking: Booking) {
  const coursePrice = booking.course?.price ?? 0;
  const courseDuration = booking.course?.duration_min ?? 0;

  const total =
    coursePrice * booking.party_size +
    booking.reservations
      .flatMap((reservation) => reservation.addons)
      .reduce((sum, addon) => sum + addon.price, 0);

  // Mỗi người một bộ add-on nên độ dài khác nhau; lượt kéo dài bằng người lâu nhất.
  const maxDuration = booking.reservations.length
    ? Math.max(
        ...booking.reservations.map(
          (reservation) =>
            courseDuration +
            reservation.addons.reduce(
              (sum, addon) => sum + addon.duration_min,
              0,
            ),
        ),
      )
    : courseDuration;

  return { total, maxDuration };
}

export function BookingDetail({ booking }: { booking: Booking }) {
  const { total, maxDuration } = bookingTotals(booking);

  return (
    <div className="px-5 py-4 sm:px-6">
      <Row label="Mã đặt chỗ">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="font-mono font-semibold tracking-widest">
            {booking.booking_code}
          </span>
          <BookingStatusBadge status={booking.status} />
        </div>
      </Row>

      <Row label="Cửa hàng">
        <div>
          【{booking.shop.shop_code}】{booking.shop.name}
          <div className="mt-0.5 text-xs text-ink-3">
            {booking.shop.address} · ☎ {booking.shop.phone}
          </div>
        </div>
      </Row>

      <Row label="Ngày · giờ">
        {formatDateVi(booking.date)} · {booking.start_time}
        <span className="text-ink-3">
          {" "}
          – {addMinutesToTime(booking.start_time, maxDuration)} ({maxDuration}{" "}
          phút)
        </span>
      </Row>

      <Row label="Số người">{booking.party_size}</Row>

      <Row label={booking.party_size > 1 ? "Course chung" : "Course"}>
        {booking.course ? (
          <>
            {booking.course.name} {booking.course.duration_min}p —{" "}
            {formatYen(booking.course.price)}
            {booking.party_size > 1 ? "/người" : ""}
          </>
        ) : (
          "—"
        )}
      </Row>

      {booking.reservations.map((reservation) => (
        <Row
          key={reservation.guest_no}
          label={
            booking.party_size > 1
              ? `Thêm — người ${reservation.guest_no}`
              : "Dịch vụ thêm"
          }
        >
          {reservation.addons.length === 0 ? (
            <span className="text-ink-3">— không</span>
          ) : (
            reservation.addons
              .map(
                (addon) =>
                  `${addon.name} ${addon.duration_min}p +${formatYen(addon.price)}`,
              )
              .join(" · ")
          )}
        </Row>
      ))}

      <Row label="Nhân viên">
        {booking.therapist_name ? (
          <>
            {booking.therapist_name}
            {/* Phân công != chỉ định (BR-21): nói rõ để khách không tưởng mình đã đòi ai. */}
            <span className="ml-2 text-xs text-ink-3">
              {booking.requested_therapist_id
                ? "(bạn đã chỉ định)"
                : "(cửa hàng phân công)"}
            </span>
          </>
        ) : booking.requested_therapist_gender === "male" ? (
          "Nhân viên nam (bạn đã chỉ định)"
        ) : booking.requested_therapist_gender === "female" ? (
          "Nhân viên nữ (bạn đã chỉ định)"
        ) : (
          <span className="text-ink-3">Cửa hàng phân công</span>
        )}
      </Row>

      <Row label="Tổng">
        <span className="font-semibold tabular-nums">{formatYen(total)}</span>
        <span className="ml-2 text-xs text-ink-3">thanh toán tại cửa hàng</span>
      </Row>
    </div>
  );
}
