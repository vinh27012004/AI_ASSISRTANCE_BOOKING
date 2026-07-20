"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { useSelectedShopId } from "@/lib/use-selected-shop";
import { Card, Button, Spinner, Alert } from "@/components/ui";

const PER_PAGE = 20;

export default function AdminBookingsPage() {
  const shopId = useSelectedShopId();

  // Filters
  const [date, setDate] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [actionError, setActionError] = useState<string | null>(null);

  // useRequest huỷ request cũ khi key đổi và bỏ qua kết quả về muộn — đổi filter
  // nhanh tay không còn cảnh kết quả cũ về sau đè lên kết quả mới.
  const bookingsReq = useRequest(
    shopId ? `${shopId}|${date}|${status}|${page}` : null,
    (signal) =>
      api.adminListBookings(
        {
          shop_id: shopId!,
          date: date || undefined,
          status: status || undefined,
          page,
          per_page: PER_PAGE,
        },
        signal,
      ),
  );

  const bookings = bookingsReq.data?.items ?? [];
  const total = bookingsReq.data?.total ?? 0;
  const loading = bookingsReq.loading;
  const error = actionError ?? bookingsReq.error?.message ?? null;

  const handleUpdateStatus = async (bookingId: number, newStatus: string) => {
    if (!confirm(`Bạn có chắc chắn muốn chuyển trạng thái đặt chỗ sang "${newStatus}"?`)) return;
    setActionError(null);
    try {
      await api.adminUpdateBookingStatus(bookingId, newStatus);
      bookingsReq.reload();
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  const getStatusBadgeClass = (s: string) => {
    switch (s) {
      case "confirmed":
        return "bg-accent-soft text-accent border border-accent-line";
      case "pending":
        return "bg-warn-soft text-warn border border-warn-line";
      case "cancelled":
        return "bg-danger-soft text-danger border border-danger-line";
      case "completed":
        return "bg-success-soft text-success border border-success-line";
      case "no_show":
        return "bg-surface-2 text-ink-3 border border-line";
      default:
        return "bg-surface border border-line";
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-ink">Quản lý Đặt lịch</h2>
          <p className="text-sm text-ink-2">Xem và cập nhật trạng thái các đơn đặt lịch của khách hàng</p>
        </div>
      </div>

      {/* Filters */}
      <Card className="p-4 flex flex-wrap gap-4 items-end bg-surface">
        <div className="w-full sm:w-auto">
          <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Lọc theo ngày</label>
          <input
            type="date"
            value={date}
            onChange={(e) => { setDate(e.target.value); setPage(1); }}
            className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          />
        </div>

        <div className="w-full sm:w-auto">
          <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Lọc theo trạng thái</label>
          <select
            value={status}
            onChange={(e) => { setStatus(e.target.value); setPage(1); }}
            className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
          >
            <option value="">Tất cả trạng thái</option>
            <option value="pending">Chờ xử lý (Pending)</option>
            <option value="confirmed">Đã xác nhận (Confirmed)</option>
            <option value="completed">Đã hoàn thành (Completed)</option>
            <option value="cancelled">Đã hủy (Cancelled)</option>
            <option value="no_show">Vắng mặt (No Show)</option>
          </select>
        </div>

        <Button
          variant="outline"
          onClick={() => { setDate(""); setStatus(""); setPage(1); }}
          className="text-xs"
        >
          Xóa bộ lọc
        </Button>
      </Card>

      {error && <Alert tone="danger">{error}</Alert>}

      {loading ? (
        <div className="flex justify-center py-12">
          <Spinner className="size-8 text-accent" />
        </div>
      ) : bookings.length === 0 ? (
        <Card className="p-12 text-center text-ink-3">
          Không tìm thấy lượt đặt lịch nào trùng khớp với bộ lọc.
        </Card>
      ) : (
        <div className="space-y-4">
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                <tr>
                  <th className="px-6 py-4">Mã / Ngày hẹn</th>
                  <th className="px-6 py-4">Khách hàng</th>
                  <th className="px-6 py-4">Chi tiết dịch vụ</th>
                  <th className="px-6 py-4">Trạng thái</th>
                  <th className="px-6 py-4 text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {bookings.map((booking) => (
                  <tr key={booking.id} className="hover:bg-surface-2 transition-colors">
                    <td className="px-6 py-4 whitespace-nowrap">
                      <div className="font-bold text-ink">{booking.booking_code}</div>
                      <div className="text-xs text-ink-2 mt-0.5">
                        {booking.date} lúc <span className="font-semibold text-accent">{booking.start_time}</span>
                      </div>
                      <div className="text-xs text-ink-3 mt-0.5">Nhóm: {booking.party_size} người</div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="font-medium text-ink">{booking.customer.phone}</div>
                      <div className="text-xs text-ink-2">{booking.customer.email}</div>
                      <div className="text-xs text-ink-3 mt-1">
                        Rank: <span className="font-semibold">{booking.customer.rank || "Guest"}</span> | Đã tới: {booking.customer.visit_count} lần
                      </div>
                    </td>
                    <td className="px-6 py-4">
                      <div className="font-medium text-ink">{booking.course?.name}</div>
                      <div className="text-xs text-ink-3">Thời lượng: {booking.course?.duration_min} phút</div>
                      <div className="mt-2 space-y-1">
                        {booking.reservations.map((res, i) => (
                          <div key={i} className="text-xs text-ink-2 flex flex-col border-l-2 border-line pl-2 ml-1">
                            <span className="font-semibold">Khách #{res.guest_no}:</span>
                            {res.therapist_name && (
                              <span className="text-[11px] text-accent">Nhân viên: {res.therapist_name}</span>
                            )}
                            {res.addons.length > 0 ? (
                              <span className="text-[11px] text-ink-3">
                                Addons: {res.addons.map((a) => a.name).join(", ")}
                              </span>
                            ) : (
                              <span className="text-[11px] text-ink-3">Không có addon</span>
                            )}
                          </div>
                        ))}
                      </div>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`inline-block px-2.5 py-1 text-xs font-semibold rounded-full ${getStatusBadgeClass(booking.status)}`}>
                        {booking.status.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right whitespace-nowrap space-x-1">
                      {(booking.status === "pending" || booking.status === "confirmed") && (
                        <>
                          <Button
                            variant="outline"
                            onClick={() => handleUpdateStatus(booking.id, "completed")}
                            className="bg-success-soft text-success border-success-line hover:bg-success hover:text-white text-xs px-2.5 py-1"
                          >
                            Hoàn tất
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => handleUpdateStatus(booking.id, "no_show")}
                            className="bg-surface-2 text-ink-2 hover:bg-ink-2 hover:text-white text-xs px-2.5 py-1"
                          >
                            Vắng
                          </Button>
                          <Button
                            variant="outline"
                            onClick={() => handleUpdateStatus(booking.id, "cancelled")}
                            className="bg-danger-soft text-danger border-danger-line hover:bg-danger hover:text-white text-xs px-2.5 py-1"
                          >
                            Hủy
                          </Button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          {total > PER_PAGE && (
            <div className="flex items-center justify-between py-4">
              <div className="text-xs text-ink-2">
                Hiển thị {bookings.length} trên tổng số {total} lượt đặt lịch
              </div>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  disabled={page <= 1}
                  onClick={() => setPage(page - 1)}
                  className="px-3 py-1.5 text-xs"
                >
                  Trước
                </Button>
                <Button
                  variant="outline"
                  disabled={page * PER_PAGE >= total}
                  onClick={() => setPage(page + 1)}
                  className="px-3 py-1.5 text-xs"
                >
                  Sau
                </Button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}