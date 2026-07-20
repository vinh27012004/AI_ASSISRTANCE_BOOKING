"use client";

import { useEffect, useMemo, useSyncExternalStore } from "react";
import { useRouter } from "next/navigation";
import { Spinner } from "@/components/ui";

type Role = "admin" | "therapist";

function subscribe(onChange: () => void) {
  // Đăng xuất ở tab khác thì tab này cũng phải rời trang admin ngay.
  window.addEventListener("storage", onChange);
  return () => window.removeEventListener("storage", onChange);
}

/**
 * Trả về CHUỖI thô, không phải object đã parse: getSnapshot phải trả giá trị so
 * sánh được bằng ===, trả object mới mỗi lần gọi là React re-render vô hạn.
 */
function getSnapshot(): string | null {
  const token = localStorage.getItem("access_token");
  const user = localStorage.getItem("user_info");
  return token && user ? user : null;
}

function getServerSnapshot(): string | null {
  return null;
}

export function AuthGuard({
  children,
  allowedRoles,
}: {
  children: React.ReactNode;
  allowedRoles: Array<Role>;
}) {
  const router = useRouter();

  // localStorage là external store: useSyncExternalStore lo luôn phần hydrate
  // (lần render đầu ở client dùng snapshot của server rồi mới đồng bộ lại), nên
  // không còn cảnh setState trong effect để dựng lại trạng thái vốn đã có sẵn.
  const raw = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // BẮT BUỘC: ở lần render hydrate, `raw` còn là snapshot của server (null) dù
  // localStorage có token. Không phân biệt "chưa đọc được" với "chưa đăng nhập"
  // thì guard đá thẳng về /login ngay lần render đầu, rồi /login lại đá ngược về
  // trang chủ theo role — bấm vào /admin/therapists lại rơi ra /admin/bookings.
  const hydrated = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );

  const role = useMemo<Role | null>(() => {
    if (!raw) return null;
    try {
      return (JSON.parse(raw) as { role: Role }).role ?? null;
    } catch {
      // user_info hỏng (người dùng tự sửa localStorage) — coi như chưa đăng nhập.
      return null;
    }
  }, [raw]);

  const authorized = role !== null && allowedRoles.includes(role);

  // `allowedRoles` là mảng literal mới mỗi lần render nên không dùng trực tiếp
  // làm dependency được.
  const allowedKey = allowedRoles.join(",");

  useEffect(() => {
    // Chưa hydrate xong thì chưa biết gì về phiên đăng nhập — chưa kết luận.
    if (!hydrated || authorized) return;
    // Effect ở đây CHỈ điều hướng — đồng bộ với router (hệ thống bên ngoài),
    // không setState.
    if (role === null) {
      router.push("/login");
      return;
    }
    router.push(role === "admin" ? "/admin/bookings" : "/therapist/schedule");
  }, [hydrated, authorized, role, allowedKey, router]);

  if (!authorized) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas">
        <Spinner className="size-8 text-accent" />
      </div>
    );
  }

  return <>{children}</>;
}
