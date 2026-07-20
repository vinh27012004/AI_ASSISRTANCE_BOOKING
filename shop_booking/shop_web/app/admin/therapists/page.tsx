"use client";

import { useState } from "react";
import { api, toApiError } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import { useSelectedShopId } from "@/lib/use-selected-shop";
import type { AdminTherapist } from "@/lib/types";

import { Card, Button, Spinner, Alert, TextInput } from "@/components/ui";

export default function AdminTherapistsPage() {
  const shopId = useSelectedShopId();
  const [actionError, setActionError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  // Create form
  const [name, setName] = useState("");
  const [gender, setGender] = useState<"male" | "female">("female");
  const [createAccount, setCreateAccount] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [creating, setCreating] = useState(false);

  // Edit state
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editName, setEditName] = useState("");
  const [editGender, setEditGender] = useState<"male" | "female">("female");

  // Form tài khoản inline: "grant" = cấp mới cho người chưa có,
  // "reset" = đặt lại mật khẩu cho người đã có. Dùng chung một hàng form.
  const [accountMode, setAccountMode] = useState<"grant" | "reset">("grant");
  const [grantingId, setGrantingId] = useState<number | null>(null);
  const [grantUsername, setGrantUsername] = useState("");
  const [grantPassword, setGrantPassword] = useState("");
  const [granting, setGranting] = useState(false);

  const therapistsReq = useRequest(shopId ? String(shopId) : null, (signal) =>
    api.adminListTherapists(shopId!, signal),
  );

  const therapists = therapistsReq.data ?? [];
  const loading = therapistsReq.loading;
  // Lỗi của thao tác (tạo/sửa/xoá) ưu tiên hơn lỗi tải danh sách: nó là cái vừa
  // xảy ra do người dùng bấm, sát với việc họ đang làm hơn.
  const error = actionError ?? therapistsReq.error?.message ?? null;
  const fetchTherapists = therapistsReq.reload;

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionError(null);
    setSuccess(null);

    if (!shopId || !name) {
      setActionError("Vui lòng nhập tên nhân viên.");
      return;
    }
    if (createAccount && (!username || !password)) {
      setActionError("Vui lòng nhập tên đăng nhập và mật khẩu cho tài khoản.");
      return;
    }

    setCreating(true);
    try {
      await api.adminCreateTherapist({
        shop_id: shopId,
        name,
        gender,
        ...(createAccount
          ? { account: { username: username.trim(), password } }
          : {}),
      });
      setSuccess("Tạo nhân viên thành công!");
      setName("");
      setGender("female");
      setCreateAccount(false);
      setUsername("");
      setPassword("");
      fetchTherapists();
    } catch (err) {
      setActionError(toApiError(err).message);
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (t: AdminTherapist) => {
    setGrantingId(null); // hai form trên cùng một hàng, không mở đồng thời
    setEditingId(t.id);
    setEditName(t.name);
    setEditGender(t.gender);
  };

  const cancelEdit = () => {
    setEditingId(null);
  };

  const saveEdit = async (id: number) => {
    setActionError(null);
    setSuccess(null);
    try {
      await api.adminUpdateTherapist(id, { name: editName, gender: editGender });
      setSuccess("Cập nhật thông tin nhân viên thành công!");
      setEditingId(null);
      fetchTherapists();
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  const startGrant = (t: AdminTherapist) => {
    setEditingId(null);
    setAccountMode("grant");
    setGrantingId(t.id);
    // Gợi ý username từ tên, admin sửa lại được. Bỏ dấu tiếng Việt vì username
    // dùng để gõ lúc đăng nhập.
    const slug = t.name
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/đ/gi, "d")
      .replace(/[^a-zA-Z0-9]/g, "")
      .toLowerCase();
    setGrantUsername(slug ? `${slug}01` : "");
    setGrantPassword("");
    setActionError(null);
    setSuccess(null);
  };

  const startReset = (t: AdminTherapist) => {
    setEditingId(null);
    setAccountMode("reset");
    setGrantingId(t.id);
    // Đặt lại mật khẩu không đụng tới username, và BE cũng không trả username về
    // (không bao giờ lộ ra API) nên ô này để trống luôn.
    setGrantUsername("");
    setGrantPassword("");
    setActionError(null);
    setSuccess(null);
  };

  const cancelGrant = () => {
    setGrantingId(null);
    setGrantUsername("");
    setGrantPassword("");
  };

  const saveGrant = async (t: AdminTherapist) => {
    if (granting) return;
    setActionError(null);
    setSuccess(null);

    const isGrant = accountMode === "grant";
    if (!grantPassword || (isGrant && !grantUsername.trim())) {
      setActionError(
        isGrant
          ? "Vui lòng nhập tên đăng nhập và mật khẩu."
          : "Vui lòng nhập mật khẩu mới.",
      );
      return;
    }

    setGranting(true);
    try {
      await api.adminUpdateTherapist(
        t.id,
        isGrant
          ? { account: { username: grantUsername.trim(), password: grantPassword } }
          : { reset_password: grantPassword },
      );
      setSuccess(
        isGrant
          ? `Đã cấp tài khoản "${grantUsername.trim()}" cho ${t.name}. Hãy báo mật khẩu cho nhân viên và nhắc họ đổi lại.`
          : `Đã đặt lại mật khẩu cho ${t.name}. Lưu ý: nếu ${t.name} đang đăng nhập ở đâu đó thì phiên đó vẫn dùng được tới 8 tiếng.`,
      );
      cancelGrant();
      fetchTherapists();
    } catch (err) {
      // Message của BE đã đủ rõ (ACCOUNT_EXISTS / USERNAME_TAKEN / ACCOUNT_MISSING).
      setActionError(toApiError(err).message);
    } finally {
      setGranting(false);
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Bạn có chắc chắn muốn xóa nhân viên này? (Chỉ xóa được khi chưa có ca làm hoặc lịch hẹn)")) return;
    setActionError(null);
    setSuccess(null);
    try {
      await api.adminDeleteTherapist(id);
      setSuccess("Xóa nhân viên thành công!");
      fetchTherapists();
    } catch (err) {
      setActionError(toApiError(err).message);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-ink">Nhân viên trị liệu</h2>
        <p className="text-sm text-ink-2">Quản lý danh sách nhân viên và tài khoản đăng nhập của họ</p>
      </div>

      {error && <Alert tone="danger">{error}</Alert>}
      {success && <Alert tone="success">{success}</Alert>}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Create Form */}
        <div className="lg:col-span-1">
          <Card className="p-6 bg-surface">
            <h3 className="text-lg font-bold text-ink mb-4 border-b border-line pb-2">Thêm nhân viên mới</h3>
            <form onSubmit={handleCreate} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Họ tên</label>
                <TextInput
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Nguyễn Văn A"
                  required
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Giới tính</label>
                <select
                  value={gender}
                  onChange={(e) => setGender(e.target.value as "male" | "female")}
                  className="w-full rounded-xl border border-line-strong bg-canvas px-3 py-2.5 text-sm text-ink focus:border-accent focus:outline-none"
                >
                  <option value="female">Nữ</option>
                  <option value="male">Nam</option>
                </select>
              </div>

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="createAccount"
                  checked={createAccount}
                  onChange={(e) => setCreateAccount(e.target.checked)}
                  className="rounded border-line-strong"
                />
                <label htmlFor="createAccount" className="text-sm text-ink-2">
                  Cấp tài khoản đăng nhập ngay
                </label>
              </div>

              {createAccount && (
                <div className="space-y-3 border-l-2 border-accent-line pl-3">
                  <div>
                    <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Tên đăng nhập</label>
                    <TextInput
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="username"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">Mật khẩu</label>
                    <TextInput
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="••••••••"
                    />
                  </div>
                </div>
              )}

              <Button
                type="submit"
                variant="primary"
                className="w-full justify-center"
                disabled={creating}
              >
                {creating ? <Spinner /> : "Thêm nhân viên"}
              </Button>
            </form>
          </Card>
        </div>

        {/* Therapist List */}
        <div className="lg:col-span-2">
          {loading ? (
            <div className="flex justify-center py-12">
              <Spinner className="size-8 text-accent" />
            </div>
          ) : therapists.length === 0 ? (
            <Card className="p-12 text-center text-ink-3">
              Chưa có nhân viên nào tại cửa hàng này.
            </Card>
          ) : (
            <div className="overflow-x-auto rounded-2xl border border-line bg-surface">
              <table className="w-full border-collapse text-left text-sm">
                <thead className="bg-surface-2 text-ink-2 font-medium border-b border-line">
                  <tr>
                    <th className="px-6 py-4">Họ tên</th>
                    <th className="px-6 py-4">Giới tính</th>
                    <th className="px-6 py-4">Tài khoản</th>
                    <th className="px-6 py-4 text-right">Hành động</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {therapists.map((t) => (
                    <tr key={t.id} className="hover:bg-surface-2 transition-colors">
                      {editingId === t.id ? (
                        <>
                          <td className="px-6 py-4">
                            <TextInput value={editName} onChange={(e) => setEditName(e.target.value)} />
                          </td>
                          <td className="px-6 py-4">
                            <select
                              value={editGender}
                              onChange={(e) => setEditGender(e.target.value as "male" | "female")}
                              className="w-full rounded-lg border border-line-strong bg-canvas px-2 py-1.5 text-sm text-ink"
                            >
                              <option value="female">Nữ</option>
                              <option value="male">Nam</option>
                            </select>
                          </td>
                          <td className="px-6 py-4 text-ink-3 text-xs">
                            {t.has_account ? "Đã có tài khoản" : "Chưa có tài khoản"}
                          </td>
                          <td className="px-6 py-4 text-right space-x-1">
                            <Button variant="primary" onClick={() => saveEdit(t.id)} className="text-xs px-2.5 py-1">
                              Lưu
                            </Button>
                            <Button variant="outline" onClick={cancelEdit} className="text-xs px-2.5 py-1">
                              Hủy
                            </Button>
                          </td>
                        </>
                      ) : grantingId === t.id ? (
                        <>
                          <td className="px-6 py-4 font-semibold text-ink align-top">
                            {t.name}
                          </td>
                          <td className="px-6 py-4" colSpan={2}>
                            <div className="flex flex-col gap-2 sm:flex-row">
                              {accountMode === "grant" && (
                                <div className="flex-1">
                                  <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">
                                    Tên đăng nhập
                                  </label>
                                  <TextInput
                                    value={grantUsername}
                                    onChange={(e) => setGrantUsername(e.target.value)}
                                    placeholder="username"
                                    autoComplete="off"
                                    disabled={granting}
                                  />
                                </div>
                              )}
                              <div className="flex-1">
                                <label className="block text-xs font-semibold text-ink-2 mb-1.5 uppercase">
                                  {accountMode === "grant" ? "Mật khẩu" : "Mật khẩu mới"}
                                </label>
                                <TextInput
                                  type="password"
                                  value={grantPassword}
                                  onChange={(e) => setGrantPassword(e.target.value)}
                                  placeholder="••••••••"
                                  autoComplete="new-password"
                                  disabled={granting}
                                />
                              </div>
                            </div>
                            <p className="mt-2 text-xs text-ink-3">
                              {accountMode === "grant"
                                ? "Mật khẩu chỉ hiện một lần ở đây — hệ thống không cho xem lại, chỉ đặt lại được."
                                : "Mật khẩu cũ sẽ mất hiệu lực ngay. Phiên đang đăng nhập của nhân viên vẫn chạy tiếp tới khi token hết hạn (8 tiếng)."}
                            </p>
                          </td>
                          <td className="px-6 py-4 text-right space-x-1 whitespace-nowrap align-top">
                            <Button
                              variant="primary"
                              onClick={() => saveGrant(t)}
                              disabled={granting}
                              className="text-xs px-2.5 py-1"
                            >
                              {granting ? (
                                <Spinner />
                              ) : accountMode === "grant" ? (
                                "Cấp"
                              ) : (
                                "Đặt lại"
                              )}
                            </Button>
                            <Button
                              variant="outline"
                              onClick={cancelGrant}
                              disabled={granting}
                              className="text-xs px-2.5 py-1"
                            >
                              Hủy
                            </Button>
                          </td>
                        </>
                      ) : (
                        <>
                          <td className="px-6 py-4 font-semibold text-ink">{t.name}</td>
                          <td className="px-6 py-4 text-ink-2">{t.gender === "male" ? "Nam" : "Nữ"}</td>
                          <td className="px-6 py-4">
                            <span
                              className={`inline-block px-2.5 py-1 text-xs font-semibold rounded-full ${
                                t.has_account
                                  ? "bg-success-soft text-success border border-success-line"
                                  : "bg-surface-2 text-ink-3 border border-line"
                              }`}
                            >
                              {t.has_account ? "Có tài khoản" : "Chưa có tài khoản"}
                            </span>
                          </td>
                          <td className="px-6 py-4 text-right space-x-1 whitespace-nowrap">
                            {t.has_account ? (
                              <Button
                                variant="outline"
                                onClick={() => startReset(t)}
                                className="text-xs px-2.5 py-1"
                              >
                                Đặt lại MK
                              </Button>
                            ) : (
                              <Button
                                variant="outline"
                                onClick={() => startGrant(t)}
                                className="text-accent border-accent-line hover:bg-accent-soft text-xs px-2.5 py-1"
                              >
                                Cấp tài khoản
                              </Button>
                            )}
                            <Button variant="outline" onClick={() => startEdit(t)} className="text-xs px-2.5 py-1">
                              Sửa
                            </Button>
                            <Button
                              variant="outline"
                              onClick={() => handleDelete(t.id)}
                              className="text-danger border-danger-line hover:bg-danger-soft text-xs px-2.5 py-1"
                            >
                              Xóa
                            </Button>
                          </td>
                        </>
                      )}
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