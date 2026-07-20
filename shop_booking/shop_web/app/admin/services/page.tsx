"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { useSelectedShopId } from "@/lib/use-selected-shop";
import type { AdminCourse } from "@/lib/types";
import { Card, Button, Spinner, Alert, TextInput } from "@/components/ui";

type Tab = "courses" | "addons" | "combos";

export default function AdminServicesPage() {
  const shopId = useSelectedShopId();
  const [tab, setTab] = useState<Tab>("courses");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-ink">Danh sách dịch vụ</h2>
        <p className="text-sm text-ink-2">Quản lý course chính, dịch vụ bổ sung (add-on) và các tổ hợp bị cấm</p>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="flex gap-2 border-b border-line">
        {[
          { id: "courses" as Tab, label: "Course chính" },
          { id: "addons" as Tab, label: "Dịch vụ bổ sung" },
          { id: "combos" as Tab, label: "Tổ hợp cấm" },
        ].map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
              tab === t.id
                ? "border-accent text-accent"
                : "border-transparent text-ink-2 hover:text-ink"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {shopId ? (
        <>
          {tab === "courses" && (
            <ServiceCrudSection
              key={`courses-${shopId}`}
              shopId={shopId}
              type="course"
              onError={setError}
              onSuccess={setSuccess}
            />
          )}
          {tab === "addons" && (
            <ServiceCrudSection
              key={`addons-${shopId}`}
              shopId={shopId}
              type="addon"
              onError={setError}
              onSuccess={setSuccess}
            />
          )}
          {tab === "combos" && (
            <ComboRestrictionsSection shopId={shopId} onError={setError} onSuccess={setSuccess} />
          )}
        </>
      ) : (
        <Card className="p-12 text-center text-ink-3">Vui lòng chọn cửa hàng.</Card>
      )}
    </div>
  );
}

function ServiceCrudSection({
  shopId,
  type,
  onError,
  onSuccess,
}: {
  shopId: number;
  type: "course" | "addon";
  onError: (msg: string | null) => void;
  onSuccess: (msg: string | null) => void;
}) {
  const [includeInactive, setIncludeInactive] = useState(false);

  // Create form
  const [name, setName] = useState("");
  const [durationMin, setDurationMin] = useState("");
  const [price, setPrice] = useState("");
  const [creating, setCreating] = useState(false);

  const itemsReq = useRequest(
    `${type}|${shopId}|${includeInactive}`,
    (signal) =>
      type === "course"
        ? api.adminListCourses(shopId, includeInactive, signal)
        : api.adminListAddons(shopId, includeInactive, signal),
  );

  const items: AdminCourse[] = itemsReq.data ?? [];
  const loading = itemsReq.loading;
  const fetchItems = itemsReq.reload;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    onError(null);
    onSuccess(null);

    const duration = Number(durationMin);
    const priceNum = Number(price);
    if (!name || !duration || duration % 15 !== 0 || priceNum < 0) {
      onError("Vui lòng nhập tên hợp lệ, thời lượng là bội số của 15 phút và giá hợp lệ.");
      return;
    }

    setCreating(true);
    try {
      const payload = { shop_id: shopId, name, duration_min: duration, price: priceNum };
      if (type === "course") {
        await api.adminCreateCourse(payload);
      } else {
        await api.adminCreateAddon(payload);
      }
      onSuccess("Tạo dịch vụ thành công!");
      setName("");
      setDurationMin("");
      setPrice("");
      fetchItems();
    } catch (err) {
      onError(toApiError(err).message);
    } finally {
      setCreating(false);
    }
  };

  const toggleActive = async (id: number, current: boolean) => {
    onError(null);
    onSuccess(null);
    try {
      if (type === "course") {
        await api.adminUpdateCourse(id, { is_active: !current });
      } else {
        await api.adminUpdateAddon(id, { is_active: !current });
      }
      onSuccess("Cập nhật trạng thái thành công!");
      fetchItems();
    } catch (err) {
      onError(toApiError(err).message);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Bạn có chắc chắn muốn xóa? Nếu dịch vụ đã có người đặt, hệ thống sẽ báo lỗi và bạn nên tắt hiển thị (is_active) thay vì xóa.")) return;
    onError(null);
    onSuccess(null);
    try {
      if (type === "course") {
        await api.adminDeleteCourse(id);
      } else {
        await api.adminDeleteAddon(id);
      }
      onSuccess("Xóa dịch vụ thành công!");
      fetchItems();
    } catch (err) {
      onError(toApiError(err).message);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-1">
        <Card className="p-6 bg-surface">
          <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">
            Thêm {type === "course" ? "course" : "add-on"} mới
          </h3>
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Tên dịch vụ</label>
              <TextInput value={name} onChange={(e) => setName(e.target.value)} required />
            </div>
            <div>
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Thời lượng (phút)</label>
              <TextInput
                type="number"
                step={15}
                value={durationMin}
                onChange={(e) => setDurationMin(e.target.value)}
                placeholder="60"
                required
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Giá (JPY)</label>
              <TextInput
                type="number"
                value={price}
                onChange={(e) => setPrice(e.target.value)}
                placeholder="3980"
                required
              />
            </div>
            <Button type="submit" variant="primary" className="w-full justify-center" disabled={creating}>
              {creating ? <Spinner /> : "Thêm dịch vụ"}
            </Button>
          </form>
        </Card>
      </div>

      <div className="lg:col-span-2 space-y-4">
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="includeInactive"
            checked={includeInactive}
            onChange={(e) => setIncludeInactive(e.target.checked)}
            className="rounded border-line-strong"
          />
          <label htmlFor="includeInactive" className="text-sm text-ink-2">
            Hiển thị cả dịch vụ đã tắt (is_active = false)
          </label>
        </div>

        {/* Lỗi TẢI hiện ngay tại chỗ; onError ở trên dành cho lỗi THAO TÁC. */}
        {itemsReq.error && (
          <Alert tone="danger">
            {itemsReq.error.message}
            <div className="mt-2">
              <Button onClick={itemsReq.reload} className="!py-1.5">
                Thử lại
              </Button>
            </div>
          </Alert>
        )}

        {loading ? (
          <div className="flex justify-center py-12">
            <Spinner className="size-8 text-accent" />
          </div>
        ) : itemsReq.error ? null : items.length === 0 ? (
          <Card className="p-12 text-center text-ink-3">Chưa có dịch vụ nào.</Card>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                <tr>
                  <th className="px-6 py-4">Tên</th>
                  <th className="px-6 py-4">Thời lượng</th>
                  <th className="px-6 py-4">Giá</th>
                  <th className="px-6 py-4">Trạng thái</th>
                  <th className="px-6 py-4 text-right">Hành động</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-surface-2 transition-colors">
                    <td className="px-6 py-4 font-semibold text-ink">{item.name}</td>
                    <td className="px-6 py-4 text-ink-2">{item.duration_min} phút</td>
                    <td className="px-6 py-4 text-ink-2">¥{item.price.toLocaleString()}</td>
                    <td className="px-6 py-4">
                      <span
                        className={`inline-block px-2.5 py-1 text-xs font-semibold rounded-full ${
                          item.is_active
                            ? "bg-success-soft text-success border border-success-line"
                            : "bg-surface-2 text-ink-3 border border-line"
                        }`}
                      >
                        {item.is_active ? "Đang hoạt động" : "Đã tắt"}
                      </span>
                    </td>
                    <td className="px-6 py-4 text-right space-x-1 whitespace-nowrap">
                      <Button
                        variant="outline"
                        onClick={() => toggleActive(item.id, item.is_active)}
                        className="text-xs px-2.5 py-1"
                      >
                        {item.is_active ? "Tắt" : "Bật"}
                      </Button>
                      <Button
                        variant="outline"
                        onClick={() => handleDelete(item.id)}
                        className="text-danger border-danger-line hover:bg-danger-soft text-xs px-2.5 py-1"
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
  );
}

function ComboRestrictionsSection({
  shopId,
  onError,
  onSuccess,
}: {
  shopId: number;
  onError: (msg: string | null) => void;
  onSuccess: (msg: string | null) => void;
}) {
  const [selectedCourse, setSelectedCourse] = useState("");
  const [selectedAddon, setSelectedAddon] = useState("");
  const [creating, setCreating] = useState(false);

  const allReq = useRequest(String(shopId), (signal) =>
    Promise.all([
      api.adminListComboRestrictions(shopId, signal),
      api.adminListCourses(shopId, true, signal),
      api.adminListAddons(shopId, true, signal),
    ]),
  );

  const [combos, courses, addons] = allReq.data ?? [[], [], []];
  const loading = allReq.loading;
  const fetchAll = allReq.reload;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    onError(null);
    onSuccess(null);
    if (!selectedCourse || !selectedAddon) {
      onError("Vui lòng chọn course và add-on.");
      return;
    }
    setCreating(true);
    try {
      await api.adminCreateComboRestriction({
        course_id: Number(selectedCourse),
        addon_id: Number(selectedAddon),
      });
      onSuccess("Thêm tổ hợp cấm thành công!");
      setSelectedCourse("");
      setSelectedAddon("");
      fetchAll();
    } catch (err) {
      onError(toApiError(err).message);
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (courseId: number, addonId: number) => {
    if (!confirm("Bạn có chắc chắn muốn xóa tổ hợp cấm này?")) return;
    onError(null);
    onSuccess(null);
    try {
      await api.adminDeleteComboRestriction(courseId, addonId);
      onSuccess("Xóa tổ hợp cấm thành công!");
      fetchAll();
    } catch (err) {
      onError(toApiError(err).message);
    }
  };

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div className="lg:col-span-1">
        <Card className="p-6 bg-surface">
          <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">Thêm tổ hợp cấm</h3>
          <form onSubmit={handleCreate} className="space-y-4">
            <div>
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Course</label>
              <select
                value={selectedCourse}
                onChange={(e) => setSelectedCourse(e.target.value)}
                className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2.5 text-sm text-ink focus:border-accent focus:outline-none"
              >
                <option value="">Chọn course...</option>
                {courses.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Add-on</label>
              <select
                value={selectedAddon}
                onChange={(e) => setSelectedAddon(e.target.value)}
                className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2.5 text-sm text-ink focus:border-accent focus:outline-none"
              >
                <option value="">Chọn add-on...</option>
                {addons.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </div>
            <Button type="submit" variant="primary" className="w-full justify-center" disabled={creating}>
              {creating ? <Spinner /> : "Thêm vào danh sách cấm"}
            </Button>
          </form>
        </Card>
      </div>

      <div className="lg:col-span-2">
        {loading ? (
          <div className="flex justify-center py-12">
            <Spinner className="size-8 text-accent" />
          </div>
        ) : combos.length === 0 ? (
          <Card className="p-12 text-center text-ink-3">
            Chưa có tổ hợp bị cấm nào — mọi combo course + add-on hiện đều hợp lệ.
          </Card>
        ) : (
          <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
            <table className="w-full border-collapse text-left text-sm">
              <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                <tr>
                  <th className="px-6 py-4">Course</th>
                  <th className="px-6 py-4">Add-on</th>
                  <th className="px-6 py-4 text-right">Hành động</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {combos.map((c) => (
                  <tr key={`${c.course_id}-${c.addon_id}`} className="hover:bg-surface-2 transition-colors">
                    <td className="px-6 py-4 font-semibold text-ink">{c.course_name}</td>
                    <td className="px-6 py-4 text-ink-2">{c.addon_name}</td>
                    <td className="px-6 py-4 text-right">
                      <Button
                        variant="outline"
                        onClick={() => handleDelete(c.course_id, c.addon_id)}
                        className="text-danger border-danger-line hover:bg-danger-soft text-xs px-2.5 py-1"
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
  );
}