"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { Card, Spinner, Alert } from "@/components/ui";

export default function TherapistSchedulePage() {
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));

  const scheduleReq = useRequest(date, (signal) =>
    api.therapistSchedule({ date }, signal),
  );

  const schedule = scheduleReq.data;
  const loading = scheduleReq.loading;
  const error = scheduleReq.error?.message ?? null;

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-ink">Lịch làm việc của tôi</h2>
          <p className="text-sm text-ink-2">Xem các ca làm việc và khách hàng được phân công theo ngày</p>
        </div>
        <div>
          <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Chọn ngày</label>
          <input
            type="date"
            value={date}
            onChange={(e) => setDate(e.target.value)}
            className="rounded-xl border border-line-strong bg-surface px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          />
        </div>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="size-8 text-accent" />
        </div>
      ) : schedule ? (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Shifts Column */}
          <div className="lg:col-span-1">
            <Card className="p-6 bg-surface">
              <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">Ca làm việc</h3>
              {schedule.shifts.length === 0 ? (
                <p className="text-sm text-ink-3 py-4 text-center">Bạn không có ca làm việc trong ngày này.</p>
              ) : (
                <div className="space-y-3">
                  {schedule.shifts.map((shift, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-center gap-2 rounded-xl bg-accent-soft border border-accent-line px-4 py-3"
                    >
                      <span className="text-lg font-bold text-accent">{shift.start_time}</span>
                      <span className="text-accent">→</span>
                      <span className="text-lg font-bold text-accent">{shift.end_time}</span>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>

          {/* Bookings Column */}
          <div className="lg:col-span-2">
            <Card className="p-6 bg-surface">
              <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">
                Khách hàng được phân công ({schedule.bookings.length})
              </h3>
              {schedule.bookings.length === 0 ? (
                <p className="text-sm text-ink-3 py-8 text-center">
                  Không có lượt hẹn nào được phân công cho bạn trong ngày này.
                </p>
              ) : (
                <div className="space-y-3">
                  {schedule.bookings.map((booking, i) => (
                    <div
                      key={i}
                      className="rounded-xl border border-line bg-canvas p-4 flex items-start gap-4"
                    >
                      {/* Time block */}
                      <div className="flex flex-col items-center justify-center shrink-0 w-20 rounded-lg bg-accent text-accent-fg py-3">
                        <span className="text-lg font-bold">{booking.start_time}</span>
                        <span className="text-[11px] opacity-90">{booking.duration_min} phút</span>
                      </div>

                      {/* Details */}
                      <div className="flex-1 min-w-0">
                        <div className="font-semibold text-ink">{booking.course_name}</div>
                        {booking.addon_names.length > 0 && (
                          <div className="text-xs text-ink-2 mt-1">
                            Add-ons: {booking.addon_names.join(", ")}
                          </div>
                        )}
                        <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-ink-3">
                          <span>
                            Khách #{booking.guest_no} / Nhóm {booking.party_size} người
                          </span>
                          <span className="font-mono">
                            SĐT: {booking.customer_phone_masked}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          </div>
        </div>
      ) : null}
    </div>
  );
}