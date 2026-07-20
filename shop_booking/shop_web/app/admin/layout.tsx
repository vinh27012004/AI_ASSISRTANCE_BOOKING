"use client";

import { useEffect, useState } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { authStorage, api } from "@/lib/api";
import {
  useSelectedShopId,
  setSelectedShopId as storeSelectedShopId,
} from "@/lib/use-selected-shop";
import type { Shop } from "@/lib/types";
import { AuthGuard } from "@/components/auth-guard";
import { UserBadge } from "@/components/user-badge";
import { Button, Spinner } from "@/components/ui";

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const [shops, setShops] = useState<Shop[]>([]);
  const [loadingShops, setLoadingShops] = useState(true);
  const router = useRouter();
  const pathname = usePathname();

  // Đọc từ store dùng chung chứ không giữ state riêng: trang đặt lịch cũng có ô
  // lọc cửa hàng, giữ bản sao ở đây thì đổi bên đó sidebar sẽ đứng yên với giá
  // trị cũ.
  const selectedShopId = useSelectedShopId();

  useEffect(() => {
    const fetchShops = async () => {
      try {
        const data = await api.shops();
        setShops(data);
        // Lần đầu vào admin chưa chọn gì — mặc định cửa hàng đầu tiên.
        if (data.length > 0 && localStorage.getItem("admin_selected_shop_id") === null) {
          storeSelectedShopId(data[0].id);
        }
      } catch (err) {
        console.error("Failed to load shops", err);
      } finally {
        setLoadingShops(false);
      }
    };
    fetchShops();
  }, []);

  const handleLogout = () => {
    authStorage.clearToken();
    authStorage.clearUser();
    router.push("/login");
  };

  const menuItems = [
    { name: "Lịch đặt chỗ", path: "/admin/bookings" },
    { name: "Xếp ca làm việc", path: "/admin/shifts" },
    { name: "Nhân viên trị liệu", path: "/admin/therapists" },
    { name: "Danh sách dịch vụ", path: "/admin/services" },
    { name: "Danh sách chặn (NG)", path: "/admin/ng-list" },
  ];

  return (
    <AuthGuard allowedRoles={["admin"]}>
      <div className="flex h-screen bg-canvas overflow-hidden">
        {/* Sidebar */}
        <aside className="w-64 bg-surface border-r border-line flex flex-col justify-between shrink-0">
          <div>
            <div className="p-6 border-b border-line">
              <h1 className="text-xl font-bold text-accent">Riraku Admin</h1>
              <p className="text-xs text-ink-3 mt-1">Trang quản trị cửa hàng</p>
            </div>

            {/* Shop Selector */}
            <div className="p-4 border-b border-line">
              <label className="block text-xs font-semibold text-ink-2 uppercase tracking-wider mb-2">
                Cửa hàng hiện tại
              </label>
              {loadingShops ? (
                <div className="flex items-center gap-2 text-sm text-ink-3">
                  <Spinner className="size-4" />
                  Đang tải cửa hàng...
                </div>
              ) : (
                <select
                  value={selectedShopId || ""}
                  onChange={(e) => storeSelectedShopId(Number(e.target.value))}
                  className="w-full rounded-lg border border-line-strong bg-canvas px-3 py-2 text-sm text-ink focus:border-accent focus:outline-none"
                >
                  {shops.map((shop) => (
                    <option key={shop.id} value={shop.id}>
                      {shop.name}
                    </option>
                  ))}
                </select>
              )}
            </div>

            {/* Navigation Menu */}
            <nav className="p-4 space-y-1">
              {menuItems.map((item) => {
                const isActive = pathname === item.path;
                return (
                  <Link
                    key={item.path}
                    href={item.path}
                    className={`block px-4 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                      isActive
                        ? "bg-accent-soft text-accent"
                        : "text-ink-2 hover:bg-surface-2 hover:text-ink"
                    }`}
                  >
                    {item.name}
                  </Link>
                );
              })}
            </nav>
          </div>

          <div className="p-4 border-t border-line">
            <Button
              variant="outline"
              onClick={handleLogout}
              className="w-full text-danger border-danger-line hover:bg-danger-soft"
            >
              Đăng xuất
            </Button>
          </div>
        </aside>

        {/* Main Content Area */}
        <main className="flex-1 overflow-y-auto">
          {/* Thanh trên cùng chỉ để biết đang thao tác bằng tài khoản nào —
              sticky vì bảng đặt lịch cuộn dài, cuộn xuống vẫn phải thấy. */}
          <div className="sticky top-0 z-10 flex justify-end border-b border-line bg-surface px-6 py-2.5 md:px-8">
            <UserBadge />
          </div>
          <div className="p-6 md:p-8">{children}</div>
        </main>
      </div>
    </AuthGuard>
  );
}