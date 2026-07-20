"use client";

import { useMemo, useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { useSelectedShopId, setSelectedShopId } from "@/lib/use-selected-shop";
import { timeToMinutes } from "@/lib/format";
import { Card, Button, Spinner, Alert } from "@/components/ui";

const PER_PAGE = 20;

/** Đơn đã huỷ/đã xong thì phân công chỉ còn là lịch sử, BE cũng chặn sửa. */
const ASSIGNABLE = new Set(["pending", "confirmed"]);

export default function AdminBookingsPage() {
  const shopId = useSelectedShopId();

  // Filters
  const [date, setDate] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [actionError, setActionError] = useState<string | null>(null);
  /** id suất đang gửi PATCH — khoá đúng ô đó, không khoá cả bảng. */
  const [assigning, setAssigning] = useState<number | null>(null);

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

  // Danh sách đầy đủ nhân viên của cửa hàng (kể cả người chưa xếp ca) — admin
  // vẫn chọn được, BE mới là chỗ báo "chưa có ca" kèm khung giờ cần phủ.
  const therapistsReq = useRequest(shopId ? `th|${shopId}` : null, (signal) =>
    api.adminListTherapists(shopId!, signal),
  );
  const therapists = therapistsReq.data ?? [];

  const shopsReq = useRequest("shops", (signal) => api.shops(signal));
  const shops = shopsReq.data ?? [];

  const bookings = useMemo(() => bookingsReq.data?.items ?? [], [bookingsReq.data]);

  // Ca làm của đúng dải ngày đang hiện trên trang — để biết ai nhận nổi lượt nào
  // TRƯỚC khi admin bấm, thay vì bấm xong mới ăn lỗi từ BE.
  const dateRange = useMemo(() => {
    if (bookings.length === 0) return null;
    const dates = bookings.map((b) => b.date).sort();
    return { from: dates[0], to: dates[dates.length - 1] };
  }, [bookings]);

  const shiftsReq = useRequest(
    shopId && dateRange ? `sh|${shopId}|${dateRange.from}|${dateRange.to}` : null,
    (signal) =>
      api.adminListShifts(
        { shop_id: shopId!, from: dateRange!.from, to: dateRange!.to },
        signal,
      ),
  );

  /** `${therapist_id}|${work_date}` -> các khoảng ca (phút trong ngày). */
  const shiftIndex = useMemo(() => {
    const map = new Map<string, Array<{ start: number; end: number }>>();
    for (const s of shiftsReq.data ?? []) {
      const key = `${s.therapist_id}|${s.work_date}`;
      const list = map.get(key) ?? [];
      list.push({ start: timeToMinutes(s.start_time), end: timeToMinutes(s.end_time) });
      map.set(key, list);
    }
    return map;
  }, [shiftsReq.data]);

  /**
   * Ca của nhân viên có phủ KÍN lượt không. Chỉ kiểm ca — chuyện trùng lịch với
   * khách khác thì FE không đủ dữ liệu (bookings phân trang), vẫn để BE chặn.
   */
  const coversShift = (
    therapistId: number,
    date: string,
    startTime: string,
    durationMin: number,
  ) => {
    const ranges = shiftIndex.get(`${therapistId}|${date}`);
    if (!ranges) return false;
    const start = timeToMinutes(startTime);
    return ranges.some((r) => r.start <= start && r.end >= start + durationMin);
  };
  const total = bookingsReq.data?.total ?? 0;
  const loading = bookingsReq.loading;
  const error = actionError ?? bookingsReq.error?.message ?? null;

  const handleAssign = async (
    bookingId: number,
    reservationId: number,
    therapistId: number,
  ) => {
    setActionError(null);
    setAssigning(reservationId);
    try {
      await api.adminAssignTherapist(bookingId, reservationId, therapistId);
      bookingsReq.reload();
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setAssigning(null);
    }
  };

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
          <p className="text-sm text-ink-2">Xem, phân công nhân viên và cập nhật trạng thái các đơn đặt lịch</p>
        </div>
      </div>

      {/* Filters */}
      <Card className="p-4 flex flex-wrap gap-4 items-end bg-surface">
        {/* Ghi vào cùng chỗ lưu với ô chọn shop ở sidebar — đổi bên nào bên kia
            cũng đổi theo, tránh hai nguồn sự thật lệch nhau. */}
        <div className="w-full sm:w-auto">
          <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Lọc theo cửa hàng</label>
          <select
            value={shopId ?? ""}
            disabled={shopsReq.loading}
            onChange={(e) => { setSelectedShopId(Number(e.target.value)); setPage(1); }}
            className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none disabled:opacity-50 sm:min-w-56"
          >
            {shopsReq.loading ? <option value="">Đang tải cửa hàng…</option> : null}
            {shops.map((s) => (
              <option key={s.id} value={s.id}>
                【{s.shop_code}】{s.name}
              </option>
            ))}
          </select>
        </div>

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

        {/* Không đụng cửa hàng: đó là bối cảnh đang làm việc, không phải bộ lọc
            tạm — xoá nó thì cả sidebar cũng nhảy theo. */}
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
                      <div className="mt-2 space-y-2">
                        {booking.reservations.map((res) => (
                          <div key={res.id} className="text-xs text-ink-2 flex flex-col gap-1 border-l-2 border-line pl-2 ml-1">
                            <span className="font-semibold">Khách #{res.guest_no}:</span>

                            {ASSIGNABLE.has(booking.status) ? (
                              <select
                                value={res.therapist_id ?? ""}
                                disabled={assigning === res.id || therapistsReq.loading}
                                onChange={(e) =>
                                  handleAssign(booking.id, res.id, Number(e.target.value))
                                }
                                aria-label={`Nhân viên phụ trách khách #${res.guest_no}`}
                                className="rounded-lg border border-line-strong bg-canvas px-2 py-1 text-[11px] text-ink focus:border-accent focus:outline-none disabled:opacity-50"
                              >
                                {/* Data cũ trước BR-21 có thể chưa phân công ai. */}
                                <option value="" disabled>— Chưa phân công —</option>
                                {therapists.map((t) => {
                                  // Không có ca phủ hết lượt thì khoá luôn, kèm
                                  // lý do — admin khỏi phải bấm mới biết.
                                  const off =
                                    !shiftsReq.loading &&
                                    !coversShift(
                                      t.id,
                                      booking.date,
                                      booking.start_time,
                                      res.duration_min,
                                    );
                                  return (
                                    <option key={t.id} value={t.id} disabled={off}>
                                      {t.name}
                                      {off ? " — không có ca" : ""}
                                    </option>
                                  );
                                })}
                              </select>
                            ) : (
                              <span className="text-[11px] text-accent">
                                Nhân viên: {res.therapist_name ?? "—"}
                              </span>
                            )}

                            {/* Khách xin đích danh ai mà thực tế ai làm (BR-21) */}
                            {res.requested_therapist_name &&
                            res.requested_therapist_name !== res.therapist_name ? (
                              <span className="text-[11px] text-warn">
                                Khách xin: {res.requested_therapist_name}
                              </span>
                            ) : null}

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