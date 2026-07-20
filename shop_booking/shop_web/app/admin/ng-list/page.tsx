"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { Card, Button, Spinner, Alert, TextInput } from "@/components/ui";

export default function AdminNgListPage() {
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Create form
  const [phone, setPhone] = useState("");
  const [reason, setReason] = useState("");
  const [creating, setCreating] = useState(false);

  // Danh sách chặn là toàn hệ thống, không theo shop -> key cố định.
  const itemsReq = useRequest("ng-list", (signal) => api.adminListNgList(signal));

  const items = itemsReq.data ?? [];
  const loading = itemsReq.loading;
  const error = actionError ?? itemsReq.error?.message ?? null;
  const fetchItems = itemsReq.reload;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);
    setSuccess(null);

    if (!/^\d{8,15}$/.test(phone)) {
      setActionError("Số điện thoại phải là 8-15 chữ số (không chứa dấu gạch ngang hoặc khoảng trắng).");
      return;
    }

    setCreating(true);
    try {
      await api.adminAddNgList({ phone, reason: reason || undefined });
      setSuccess("Đã thêm số điện thoại vào danh sách chặn.");
      setPhone("");
      setReason("");
      fetchItems();
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Bạn có chắc chắn muốn gỡ số điện thoại này khỏi danh sách chặn?")) return;
    setActionError(null);
    setSuccess(null);
    try {
      await api.adminDeleteNgList(id);
      setSuccess("Đã gỡ khỏi danh sách chặn.");
      fetchItems();
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-ink">Danh sách chặn (NG List)</h2>
        <p className="text-sm text-ink-2">
          Số điện thoại bị cấm đặt chỗ online trên <strong>toàn hệ thống</strong> (mọi cửa hàng)
        </p>
      </div>

      <Alert tone="warn" title="Lưu ý quan trọng">
        Thêm số điện thoại vào đây sẽ chặn khách đặt lịch trên TẤT CẢ các cửa hàng, không riêng cửa hàng bạn đang chọn.
        Lý do chặn sẽ được hiển thị cho khách hàng khi họ bị từ chối — vui lòng viết lý do lịch sự, phù hợp để người ngoài đọc được.
      </Alert>

      {error && <Alert tone="danger">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Create Form */}
        <div className="lg:col-span-1">
          <Card className="p-6 bg-surface">
            <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">Chặn số điện thoại</h3>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Số điện thoại</label>
                <TextInput
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="09012345678"
                  required
                />
                <p className="text-[11px] text-ink-3 mt-1">Chỉ nhập chữ số, 8-15 ký tự.</p>
              </div>
              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Lý do (hiển thị cho khách)</label>
                <TextInput
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Ví dụ: Vui lòng liên hệ trực tiếp cửa hàng để đặt chỗ"
                />
              </div>
              <Button
                type="submit"
                variant="primary"
                className="w-full justify-center"
                disabled={creating}
              >
                {creating ? <Spinner /> : "Thêm vào danh sách chặn"}
              </Button>
            </form>
          </Card>
        </div>

        {/* NG List */}
        <div className="lg:col-span-2">
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner className="size-8 text-accent" />
            </div>
          ) : items.length === 0 ? (
            <Card className="p-12 text-center text-ink-3">Danh sách chặn hiện đang trống.</Card>
          ) : (
            <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
              <table className="w-full border-collapse text-left text-sm">
                <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                  <tr>
                    <th className="px-6 py-4">Số điện thoại</th>
                    <th className="px-6 py-4">Lý do</th>
                    <th className="px-6 py-4">Ngày thêm</th>
                    <th className="px-6 py-4 text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {items.map((item) => (
                    <tr key={item.id} className="hover:bg-surface-2 transition-colors">
                      <td className="px-6 py-4 font-mono font-semibold text-ink">{item.phone}</td>
                      <td className="px-6 py-4 text-ink-2">{item.reason || "—"}</td>
                      <td className="px-6 py-4 text-ink-3 text-xs">{item.added_at}</td>
                      <td className="px-6 py-4 text-right">
                        <Button
                          variant="outline"
                          onClick={() => handleDelete(item.id)}
                          className="text-danger border-danger-line hover:bg-danger-soft text-xs px-2.5 py-1"
                        >
                          Gỡ chặn
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