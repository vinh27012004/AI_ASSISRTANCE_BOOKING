"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { useSelectedShopId } from "@/lib/use-selected-shop";
import { Card, Button, Spinner, Alert, TextInput } from "@/components/ui";

export default function AdminShiftsPage() {
  const shopId = useSelectedShopId();
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Form states
  const [selectedTherapistId, setSelectedTherapistId] = useState("");
  const [workDate, setWorkDate] = useState("");
  const [startTime, setStartTime] = useState("");
  const [endTime, setEndTime] = useState("");
  const [creating, setCreating] = useState(false);

  // Filters
  const [filterDate, setFilterDate] = useState("");

  const dataReq = useRequest(
    shopId ? `${shopId}|${filterDate}` : null,
    (signal) =>
      Promise.all([
        api.adminListShifts(
          { shop_id: shopId!, date: filterDate || undefined },
          signal,
        ),
        api.adminListTherapists(shopId!, signal),
      ]),
  );

  const [shifts, therapists] = dataReq.data ?? [[], []];
  const loading = dataReq.loading;
  const error = actionError ?? dataReq.error?.message ?? null;
  const fetchData = dataReq.reload;

  const handleCreateShift = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);
    setSuccess(null);

    if (!selectedTherapistId || !workDate || !startTime || !endTime) {
      setActionError("Vui lòng nhập đầy đủ thông tin.");
      return;
    }

    setCreating(true);
    try {
      await api.adminCreateShift({
        therapist_id: Number(selectedTherapistId),
        work_date: workDate,
        start_time: startTime,
        end_time: endTime,
      });
      setSuccess("Tạo ca làm việc thành công!");
      setWorkDate("");
      setStartTime("");
      setEndTime("");
      setSelectedTherapistId("");
      fetchData();
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteShift = async (id: number) => {
    if (!confirm("Bạn có chắc chắn muốn xóa ca làm việc này?")) return;
    setActionError(null);
    setSuccess(null);
    try {
      await api.adminDeleteShift(id);
      setSuccess("Xóa ca làm việc thành công!");
      fetchData();
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-ink">Xếp ca làm việc</h2>
        <p className="text-sm text-ink-2">Quản lý và sắp xếp lịch làm việc cho các nhân viên trị liệu</p>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Creation Form */}
        <div className="lg:col-span-1">
          <Card className="p-6 bg-surface">
            <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">Xếp ca mới</h3>
            <form onSubmit={handleCreateShift} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Nhân viên</label>
                <select
                  value={selectedTherapistId}
                  onChange={(e) => setSelectedTherapistId(e.target.value)}
                  className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2.5 text-sm text-ink focus:border-accent focus:outline-none"
                  required
                >
                  <option value="">Chọn nhân viên...</option>
                  {therapists.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name} ({t.gender === "male" ? "Nam" : "Nữ"})
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Ngày làm việc</label>
                <TextInput
                  type="date"
                  value={workDate}
                  onChange={(e) => setWorkDate(e.target.value)}
                  required
                />
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Bắt đầu (HH:MM)</label>
                  <TextInput
                    type="text"
                    placeholder="10:00"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    required
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Kết thúc (HH:MM)</label>
                  <TextInput
                    type="text"
                    placeholder="18:00"
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    required
                  />
                </div>
              </div>
              <p className="text-[11px] text-ink-3">
                * Giờ làm việc phải là bội số của 15 phút (ví dụ: 10:00, 10:15, 10:30...)
              </p>

              <Button
                type="submit"
                variant="primary"
                className="w-full justify-center"
                disabled={creating}
              >
                {creating ? <Spinner /> : "Thêm ca làm việc"}
              </Button>
            </form>
          </Card>
        </div>

        {/* Shift list */}
        <div className="lg:col-span-2 space-y-4">
          <Card className="p-4 flex gap-4 items-end bg-surface">
            <div className="w-full sm:w-auto">
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Lọc theo ngày</label>
              <input
                type="date"
                value={filterDate}
                onChange={(e) => setFilterDate(e.target.value)}
                className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
              />
            </div>
            <Button
              variant="outline"
              onClick={() => setFilterDate("")}
              className="text-xs"
            >
              Xóa lọc ngày
            </Button>
          </Card>

          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner className="size-8 text-accent" />
            </div>
          ) : shifts.length === 0 ? (
            <Card className="p-12 text-center text-ink-3">
              Không có ca làm việc nào được xếp trong ngày này.
            </Card>
          ) : (
            <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
              <table className="w-full border-collapse text-left text-sm">
                <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                  <tr>
                    <th className="px-6 py-4">Nhân viên</th>
                    <th className="px-6 py-4">Ngày</th>
                    <th className="px-6 py-4">Khung giờ</th>
                    <th className="px-6 py-4 text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {shifts.map((s) => (
                    <tr key={s.id} className="hover:bg-surface-2 transition-colors">
                      <td className="px-6 py-4 font-semibold text-ink">{s.therapist_name}</td>
                      <td className="px-6 py-4 text-ink-2">{s.work_date}</td>
                      <td className="px-6 py-4 text-accent font-medium">
                        {s.start_time} - {s.end_time}
                      </td>
                      <td className="px-6 py-4 text-right">
                        <Button
                          variant="outline"
                          onClick={() => handleDeleteShift(s.id)}
                          className="text-danger border-danger-line hover:bg-danger-soft text-xs"
                        >
                          Xóa
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}