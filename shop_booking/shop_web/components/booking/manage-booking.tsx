"use client";

import { useState } from "react";
import Link from "next/link";
import { ApiError, api, toApiError } from "@/lib/api";
import type { Booking } from "@/lib/types";
import { Alert, Button, Card, Field, Spinner, TextInput } from "@/components/ui";
import { BookingDetail } from "./booking-detail";
import { ReschedulePanel } from "./reschedule-panel";

type Mode = "view" | "reschedule" | "cancel";

/**
 * Trang Quản lý đặt chỗ (US-02): tra cứu bằng mã + email, đổi giờ, huỷ.
 *
 * Khác cửa sổ "sửa nhanh" 2 phút ở màn hình Thành công (BR-17) — ở đây luôn
 * phải xác thực lại bằng email, nên `email` được giữ để gửi kèm mọi lần PATCH.
 */
export function ManageBooking() {
  const [code, setCode] = useState("");
  const [email, setEmail] = useState("");
  const [looking, setLooking] = useState(false);
  const [lookupError, setLookupError] = useState<ApiError | null>(null);

  const [booking, setBooking] = useState<Booking | null>(null);
  const [mode, setMode] = useState<Mode>("view");
  const [cancelling, setCancelling] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const lookup = async (event: React.FormEvent) => {
    event.preventDefault();
    if (looking) return;
    setLooking(true);
    setLookupError(null);
    setNotice(null);
    try {
      const found = await api.retrieveBooking({
        booking_code: code.trim().toUpperCase(),
        email: email.trim(),
      });
      setBooking(found);
      setMode("view");
    } catch (caught) {
      setLookupError(toApiError(caught));
    } finally {
      setLooking(false);
    }
  };

  const confirmCancel = async () => {
    if (!booking || cancelling) return;
    setCancelling(true);
    setActionError(null);
    try {
      const next = await api.cancelBooking(booking.booking_code, email.trim());
      setBooking(next);
      setMode("view");
      setNotice("Đã huỷ đặt chỗ. Email xác nhận huỷ đã được gửi cho bạn.");
    } catch (caught) {
      setActionError(toApiError(caught));
    } finally {
      setCancelling(false);
    }
  };

  const reset = () => {
    setBooking(null);
    setMode("view");
    setCode("");
    setEmail("");
    setLookupError(null);
    setActionError(null);
    setNotice(null);
  };

  /* ------------------------------------------------------------- Tra cứu */

  if (!booking) {
    return (
      <Card>
        <div className="border-b border-line px-5 py-4 sm:px-6">
          <h2 className="text-sm font-medium">Tra cứu đặt chỗ</h2>
          <p className="mt-1 text-xs text-ink-3">
            Nhập mã đặt chỗ trong email xác nhận cùng email bạn đã dùng khi đặt.
          </p>
        </div>

        <form onSubmit={lookup}>
          <Field label="Mã đặt chỗ">
            <TextInput
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="VD: BK7X2M9A"
              autoComplete="off"
              autoCapitalize="characters"
              required
              disabled={looking}
              className="font-mono tracking-widest uppercase"
            />
          </Field>

          <Field label="Email" last>
            <TextInput
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="ban@vidu.com"
              autoComplete="email"
              required
              disabled={looking}
            />
          </Field>

          {lookupError ? (
            <div className="px-5 pb-4 sm:px-6">
              <Alert tone="danger">{lookupError.message}</Alert>
            </div>
          ) : null}

          <div className="flex items-center justify-between gap-3 border-t border-line bg-surface-2 px-5 py-4 sm:px-6">
            <Link
              href="/"
              className="text-sm text-ink-2 underline underline-offset-2 hover:text-ink"
            >
              ← Đặt lịch mới
            </Link>
            <Button
              type="submit"
              variant="primary"
              disabled={looking || !code.trim() || !email.trim()}
              className="px-6"
            >
              {looking ? <Spinner /> : null}
              Tra cứu
            </Button>
          </div>
        </form>
      </Card>
    );
  }

  /* -------------------------------------------------------------- Chi tiết */

  return (
    <Card>
      <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-4 sm:px-6">
        <h2 className="text-sm font-medium">
          {mode === "reschedule" ? "Đổi ngày · giờ" : "Đặt chỗ của bạn"}
        </h2>
        <button
          type="button"
          onClick={reset}
          className="text-xs text-ink-2 underline underline-offset-2 hover:text-ink"
        >
          Tra cứu mã khác
        </button>
      </div>

      {notice ? (
        <div className="px-5 pt-4 sm:px-6">
          <Alert tone="success">{notice}</Alert>
        </div>
      ) : null}

      {mode === "reschedule" ? (
        <ReschedulePanel
          booking={booking}
          email={email.trim()}
          onCancel={() => setMode("view")}
          onDone={(next) => {
            setBooking(next);
            setMode("view");
            setNotice("Đã đổi giờ hẹn. Email xác nhận đã được gửi cho bạn.");
          }}
        />
      ) : (
        <>
          <BookingDetail booking={booking} />

          {actionError ? (
            <div className="px-5 pb-4 sm:px-6">
              <Alert tone="danger">{actionError.message}</Alert>
            </div>
          ) : null}

          {/* BR-16 — BE tính can_modify theo giờ máy chủ; hết hạn thì chỉ còn gọi shop. */}
          {!booking.can_modify && booking.status !== "cancelled" ? (
            <div className="px-5 pb-4 sm:px-6">
              <Alert tone="info">
                Đặt chỗ này không còn sửa được online. Vui lòng liên hệ cửa hàng
                ☎ {booking.shop.phone} nếu bạn cần thay đổi.
              </Alert>
            </div>
          ) : null}

          {mode === "cancel" ? (
            <div className="border-t border-line bg-danger-soft/40 px-5 py-4 sm:px-6">
              <p className="text-sm text-ink">
                Huỷ đặt chỗ{" "}
                <span className="font-mono font-semibold">
                  {booking.booking_code}
                </span>
                ? Thao tác này không hoàn tác được.
              </p>
              <div className="mt-3 flex flex-wrap gap-3">
                <Button onClick={() => setMode("view")} disabled={cancelling}>
                  Không, giữ lại
                </Button>
                <Button
                  onClick={confirmCancel}
                  disabled={cancelling}
                  className="border-danger-line bg-danger text-white hover:bg-danger"
                >
                  {cancelling ? <Spinner /> : null}
                  Huỷ đặt chỗ
                </Button>
              </div>
            </div>
          ) : booking.can_modify ? (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line bg-surface-2 px-5 py-4 sm:px-6">
              <Button
                onClick={() => {
                  setMode("cancel");
                  setActionError(null);
                }}
                className="text-danger"
              >
                Huỷ đặt chỗ
              </Button>
              <Button
                variant="primary"
                onClick={() => {
                  setMode("reschedule");
                  setActionError(null);
                  setNotice(null);
                }}
              >
                Đổi ngày · giờ
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}
