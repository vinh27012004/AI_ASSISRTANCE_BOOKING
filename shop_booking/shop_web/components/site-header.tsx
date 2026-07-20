import Link from "next/link";
import { buttonClass } from "@/components/ui";

/**
 * Thanh đầu trang cho phần khách hàng: thương hiệu bên trái, "Xem lại lịch" +
 * "Đăng nhập" ghim ở góc trên bên phải. Hai chấm tròn lấy lại mô-típ thanh tiêu
 * đề cửa sổ (.wt) của wireframe cho đồng bộ.
 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b-[1.5px] border-frame bg-surface">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2 sm:px-6">
        <Link
          href="/"
          className="flex items-center gap-2 text-sm text-ink transition-colors hover:text-accent"
        >
          <span
            aria-hidden
            className="size-[9px] rounded-full border-[1.2px] border-ink-3"
          />
          <span
            aria-hidden
            className="size-[9px] rounded-full border-[1.2px] border-ink-3"
          />
          <b>Đặt lịch massage online</b>
        </Link>

        <nav aria-label="Tài khoản" className="ml-auto flex items-center gap-2">
          <Link href="/booking/manage" className={buttonClass("accent")}>
            Xem lại lịch
          </Link>
          <Link href="/login" className={buttonClass("outline")}>
            Đăng nhập
          </Link>
        </nav>
      </div>
    </header>
  );
}
