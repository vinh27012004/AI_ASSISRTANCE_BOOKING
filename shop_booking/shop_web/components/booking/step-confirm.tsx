"use client";

import { useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError, api, toApiError } from "@/lib/api";
import type {
  BookingCreateRequest,
  BookingCreated,
  Gender,
  ServicesResponse,
  Shop,
  Therapist,
} from "@/lib/types";
import { Alert, Button, Chip, Spinner } from "@/components/ui";
import { addMinutesToTime, formatDateVi, formatYen } from "@/lib/format";
import type { PartySize } from "./booking-wizard";

function newUuid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

/** Bước cần quay lại để sửa từng loại lỗi BE trả về. */
const RECOVERY_STEP: Record<string, { step: number; label: string }> = {
  INVALID_COMBO: { step: 2, label: "Chọn lại dịch vụ" },
  THERAPIST_OFF_SHIFT: { step: 2, label: "Đổi giờ hoặc bỏ chỉ định" },
  PARTY_SIZE_EXCEEDED: { step: 1, label: "Sửa số người" },
  THERAPIST_NOT_ALLOWED: { step: 2, label: "Bỏ chỉ định nhân viên" },
  PHONE_BLOCKED: { step: 3, label: "Đổi số điện thoại" },
  RESOURCE_NOT_FOUND: { step: 2, label: "Chọn lại dịch vụ" },
};

/** Hàng "nhãn — giá trị" kẻ đứt như .kv của wireframe 04. */
function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 border-b border-dashed border-line py-2 text-sm last:border-b-0">
      <div className="w-28 shrink-0 text-ink-3 sm:w-32">{label}</div>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function EditLink({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="ml-2 shrink-0 text-xs text-accent underline underline-offset-2 hover:text-accent-hover"
    >
      sửa
    </button>
  );
}

export function StepConfirm({
  shop,
  date,
  startTime,
  partySize,
  courseId,
  guestAddons,
  therapistGender,
  therapist,
  phone,
  email,
  services,
  onPickSuggestedSlot,
  onEditStep,
  onBack,
  onCreated,
}: {
  shop: Shop;
  date: string;
  startTime: string;
  partySize: PartySize;
  courseId: number;
  guestAddons: number[][];
  therapistGender: Gender | null;
  therapist: Therapist | null;
  phone: string;
  email: string;
  services: ServicesResponse;
  onPickSuggestedSlot: (time: string) => void;
  onEditStep: (step: number) => void;
  onBack: () => void;
  onCreated: (booking: BookingCreated) => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const course = services.courses.find((item) => item.id === courseId) ?? null;
  const addonById = useMemo(
    () => new Map(services.addons.map((addon) => [addon.id, addon])),
    [services.addons],
  );

  const guestAddonList = guestAddons.map((ids) =>
    ids.map((id) => addonById.get(id)).filter((addon) => addon !== undefined),
  );

  const maxDuration = course
    ? Math.max(
        ...guestAddonList.map(
          (list) =>
            course.duration_min +
            list.reduce((sum, addon) => sum + addon.duration_min, 0),
        ),
      )
    : 0;

  const totalPrice = course
    ? course.price * partySize +
      guestAddonList
        .flat()
        .reduce((sum, addon) => sum + addon.price, 0)
    : 0;

  const payload = useMemo<BookingCreateRequest>(
    () => ({
      shop_id: shop.id,
      date,
      start_time: startTime,
      party_size: partySize,
      phone,
      email,
      course_id: courseId,
      reservations: guestAddons.map((addon_ids) => ({ addon_ids })),
      // BR-04: nhóm ≥2 không gửi chỉ định. BE cấm gửi cả id lẫn gender, nên
      // đích danh được ưu tiên và gender bị bỏ khi đã chọn người.
      therapist_id: partySize === 1 ? (therapist?.id ?? null) : null,
      therapist_gender:
        partySize === 1 && !therapist ? therapistGender : null,
    }),
    [
      shop.id,
      date,
      startTime,
      partySize,
      phone,
      email,
      courseId,
      guestAddons,
      therapistGender,
      therapist,
    ],
  );

  /**
   * Cùng một payload thì dùng lại Idempotency-Key — bấm "Thử lại" sau lỗi mạng
   * không tạo booking thứ hai. Đổi giờ (A6) là payload khác → key mới.
   */
  const keyRef = useRef<{ payload: string; key: string } | null>(null);
  const idempotencyKey = () => {
    const serialized = JSON.stringify(payload);
    if (keyRef.current?.payload !== serialized) {
      keyRef.current = { payload: serialized, key: newUuid() };
    }
    return keyRef.current.key;
  };

  const submit = async () => {
    if (loading) return;
    setLoading(true);
    setError(null);
    try {
      onCreated(await api.createBooking(payload, idempotencyKey()));
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setLoading(false);
    }
  };

  const suggestedSlots =
    error?.code === "SLOT_CONFLICT" && Array.isArray(error.details?.suggested_slots)
      ? (error.details.suggested_slots as string[])
      : [];

  const recovery = error ? RECOVERY_STEP[error.code] : undefined;
  const retryable = error?.code === "INTERNAL_ERROR" || error?.code === "NETWORK_ERROR";

  return (
    <>
      <div className="px-4 py-3">
        <Row label="Cửa hàng">
          <div className="flex items-start justify-between gap-2">
            <span>
              【{shop.shop_code}】{shop.name}
            </span>
            <EditLink onClick={() => onEditStep(1)} />
          </div>
        </Row>

        <Row label="Ngày · giờ">
          <div className="flex items-start justify-between gap-2">
            <span>
              {formatDateVi(date)} · {startTime}
              <span className="text-ink-3">
                {" "}
                – {addMinutesToTime(startTime, maxDuration)} ({maxDuration} phút)
              </span>
            </span>
            <EditLink onClick={() => onEditStep(2)} />
          </div>
        </Row>

        <Row label="Số người">
          <div className="flex items-start justify-between gap-2">
            <span>{partySize}</span>
            <EditLink onClick={() => onEditStep(1)} />
          </div>
        </Row>

        <Row label={partySize > 1 ? "Course chung" : "Course"}>
          <div className="flex items-start justify-between gap-2">
            <span>
              {course ? (
                <>
                  {course.name} {course.duration_min}p —{" "}
                  {formatYen(course.price)}
                  {partySize > 1 ? "/người" : ""}
                </>
              ) : (
                "—"
              )}
            </span>
            <EditLink onClick={() => onEditStep(2)} />
          </div>
        </Row>

        {guestAddonList.map((list, index) => (
          <Row
            key={index}
            label={partySize > 1 ? `Thêm — người ${index + 1}` : "Dịch vụ thêm"}
          >
            <div className="flex items-start justify-between gap-2">
              <span>
                {list.length === 0 ? (
                  <span className="text-ink-3">— không</span>
                ) : (
                  list
                    .map(
                      (addon) =>
                        `${addon.name} ${addon.duration_min}p +${formatYen(addon.price)}`,
                    )
                    .join(" · ")
                )}
              </span>
              <EditLink onClick={() => onEditStep(2)} />
            </div>
          </Row>
        ))}

        <Row label="Nhân viên">
          <div className="flex items-start justify-between gap-2">
            <span>
              {partySize > 1 ? (
                <span className="text-ink-3">
                  Không chỉ định (nhóm từ 2 người)
                </span>
              ) : therapist ? (
                therapist.name
              ) : therapistGender === "male" ? (
                "Nhân viên nam"
              ) : therapistGender === "female" ? (
                "Nhân viên nữ"
              ) : (
                <span className="text-ink-3">Không chỉ định</span>
              )}
            </span>
            {partySize === 1 ? <EditLink onClick={() => onEditStep(2)} /> : null}
          </div>
        </Row>

        <Row label="SĐT · Email">
          <div className="flex items-start justify-between gap-2">
            <span className="break-all">
              {phone} · {email}
            </span>
            <EditLink onClick={() => onEditStep(3)} />
          </div>
        </Row>

        <Row label="Tổng">
          <span className="font-semibold tabular-nums">
            {formatYen(totalPrice)}
          </span>
          <span className="ml-2 text-xs text-ink-3">thanh toán tại cửa hàng</span>
        </Row>
      </div>

      {error ? (
        <div className="px-4 pb-3">
          <Alert tone={error.code === "SLOT_CONFLICT" ? "warn" : "danger"}>
            {error.message}

            {/* Case A6 — slot vừa bị chiếm, BE gợi ý 3 giờ gần nhất */}
            {suggestedSlots.length > 0 ? (
              <div className="mt-2.5 flex flex-wrap items-center gap-2">
                <span className="text-xs">Gợi ý:</span>
                {suggestedSlots.map((time) => (
                  <Chip
                    key={time}
                    // .chip.ac xanh nhạt như hàng "Gợi ý" của wireframe 04
                    selected
                    tone="accent"
                    onClick={() => {
                      onPickSuggestedSlot(time);
                      setError(null);
                    }}
                    className="tabular-nums"
                  >
                    {time}
                  </Chip>
                ))}
              </div>
            ) : null}

            {/* Case A7 — lỗi hệ thống: booking chưa được tạo, cho thử lại */}
            {retryable ? (
              <div className="mt-2.5">
                <Button onClick={submit} className="!py-1.5">
                  Thử lại
                </Button>
              </div>
            ) : null}

            {recovery ? (
              <div className="mt-2.5">
                <Button
                  onClick={() => onEditStep(recovery.step)}
                  className="!py-1.5"
                >
                  {recovery.label}
                </Button>
              </div>
            ) : null}
          </Alert>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-dashed border-line px-4 py-3">
        <Button onClick={onBack} disabled={loading}>
          ◀ Quay lại
        </Button>
        <Button
          variant="primary"
          onClick={submit}
          disabled={loading}
          className="px-6"
        >
          {loading ? <Spinner /> : null}
          Xác nhận đặt chỗ
        </Button>
      </div>
    </>
  );
}
