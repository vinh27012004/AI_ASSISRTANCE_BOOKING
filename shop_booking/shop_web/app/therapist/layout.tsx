"use client";

import { useRouter } from "next/navigation";
import { authStorage } from "@/lib/api";
import { AuthGuard } from "@/components/auth-guard";
import { UserBadge } from "@/components/user-badge";
import { Button } from "@/components/ui";

export default function TherapistLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();

  const handleLogout = () => {
    authStorage.clearToken();
    authStorage.clearUser();
    router.push("/login");
  };

  return (
    <AuthGuard allowedRoles={["therapist"]}>
      <div className="flex flex-col min-h-screen bg-canvas">
        {/* Header */}
        <header className="bg-surface border-b border-line">
          <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
            <div>
              <h1 className="text-xl font-bold text-accent">Riraku Therapist</h1>
              <p className="text-xs text-ink-3">Lịch làm việc của nhân viên trị liệu</p>
            </div>
            <div className="flex items-center gap-4">
              <UserBadge />
              <Button
                variant="outline"
                onClick={handleLogout}
                className="text-danger border-danger-line hover:bg-danger-soft"
              >
                Đăng xuất
              </Button>
            </div>
          </div>
        </header>

        {/* Main Content */}
        <main className="flex-1 overflow-y-auto p-6 md:p-8">
          <div className="max-w-7xl mx-auto">{children}</div>
        </main>
      </div>
    </AuthGuard>
  );
}