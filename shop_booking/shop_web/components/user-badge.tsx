"use client";

import { useCurrentUser } from "@/lib/use-current-user";

const ROLE_LABEL: Record<string, string> = {
  admin: "Quản trị viên",
  therapist: "Nhân viên trị liệu",
};

/**
 * Tên người đang đăng nhập, ghim góc trên bên phải. Phiên đăng nhập cũ (lưu
 * trước khi BE trả tên) không có `display_name` nên lùi dần: tên hiển thị →
 * username → nhãn theo role.
 */
export function UserBadge({ className }: { className?: string }) {
  const user = useCurrentUser();
  if (!user) return null;

  const role = ROLE_LABEL[user.role] ?? user.role;
  const name = user.display_name || user.username || role;

  return (
    <div className={className}>
      <div className="flex items-center gap-2.5">
        {/* Chấm tròn chữ cái đầu — đủ để nhận ra đang ở tài khoản nào. */}
        <span
          aria-hidden
          className="flex size-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-sm font-bold text-accent"
        >
          {name.slice(0, 1).toUpperCase()}
        </span>
        <span className="leading-tight">
          <span className="block text-sm font-semibold text-ink">{name}</span>
          <span className="block text-xs text-ink-3">{role}</span>
        </span>
      </div>
    </div>
  );
}
