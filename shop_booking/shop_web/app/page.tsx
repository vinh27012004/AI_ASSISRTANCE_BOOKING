import { BookingWizard } from "@/components/booking/booking-wizard";
import { SiteHeader } from "@/components/site-header";

export default function Page() {
  return (
    <>
      <SiteHeader />
      <main className="flex-1">
        <BookingWizard />
      </main>
    </>
  );
}
