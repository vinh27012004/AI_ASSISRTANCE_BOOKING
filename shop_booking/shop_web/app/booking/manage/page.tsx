import type { Metadata } from "next";
import { ManageBooking } from "@/components/booking/manage-booking";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Quản lý đặt chỗ",
  description: "Tra cứu, đổi giờ hoặc huỷ đặt chỗ bằng mã đặt chỗ và email.",
};

export default function Page() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        <div className="mx-auto w-full max-w-3xl px-4 py-8 sm:py-12">
          <header className="mb-6">
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">
              Quản lý đặt chỗ
            </h1>
            <p className="mt-1.5 text-sm text-ink-2">
              Tra cứu bằng mã đặt chỗ và email để đổi giờ hoặc huỷ.
            </p>
          </header>

          <ManageBooking />
        </div>
      </main>
    </>
  );
}
