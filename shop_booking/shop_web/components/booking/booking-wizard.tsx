"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useRequest } from "@/lib/use-request";
import type {
  BookingCreated,
  CustomerInfo,
  Gender,
  Shop,
  Therapist,
} from "@/lib/types";
import { Stepper, StepWindow } from "./stepper";
import { StepShopDate } from "./step-shop-date";
import { StepServices } from "./step-services";
import { StepCustomer } from "./step-customer";
import { StepConfirm } from "./step-confirm";
import { StepSuccess } from "./step-success";

export type PartySize = 1 | 2 | 3;

const emptyAddons = (partySize: number): number[][] =>
  Array.from({ length: partySize }, () => []);

export function BookingWizard() {
  const [step, setStep] = useState(1);

  // --- Bước 1
  const [shop, setShop] = useState<Shop | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [partySize, setPartySize] = useState<PartySize>(1);
  const [closedDates, setClosedDates] = useState<ReadonlySet<string>>(new Set());

  // --- Bước 2
  const [courseId, setCourseId] = useState<number | null>(null);
  const [guestAddons, setGuestAddons] = useState<number[][]>(() => emptyAddons(1));
  // BE chỉ nhận MỘT trong hai: giới tính hoặc người đích danh. Giữ hai state
  // riêng nhưng mọi setter bên dưới luôn xoá cái còn lại.
  const [therapistGender, setTherapistGender] = useState<Gender | null>(null);
  const [therapist, setTherapist] = useState<Therapist | null>(null);
  const [startTime, setStartTime] = useState<string | null>(null);

  // --- Bước 3
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [customer, setCustomer] = useState<CustomerInfo | null>(null);

  // --- Bước 5
  const [created, setCreated] = useState<BookingCreated | null>(null);

  const shops = useRequest("shops", (signal) => api.shops(signal));

  // Dịch vụ phụ thuộc shop + ngày + số người; gọi ngay từ bước 1 để bắt case A1
  // (shop nghỉ) trước khi khách đi tiếp.
  const services = useRequest(
    shop && date ? `${shop.id}|${date}|${partySize}` : null,
    // Chỉ chạy khi key khác null, tức shop/date chắc chắn đã có.
    (signal) => api.services(shop!.id, { date: date!, partySize }, signal),
  );

  const closedDate = services.data?.reason === "SHOP_CLOSED" ? date : null;

  // Ghi nhớ ngày đã biết là shop nghỉ để lịch gạch sẵn, khỏi bắt khách thử lại.
  // Cập nhật ngay lúc render (React re-render trước khi vẽ) thay vì trong
  // effect — tránh một vòng render thừa.
  if (closedDate && !closedDates.has(closedDate)) {
    setClosedDates(new Set(closedDates).add(closedDate));
  }

  /** Đổi shop/ngày/số người thì mọi lựa chọn dịch vụ + giờ không còn tin được. */
  const resetServiceChoices = (nextPartySize: number = partySize) => {
    setCourseId(null);
    setGuestAddons(emptyAddons(nextPartySize));
    setTherapistGender(null);
    setTherapist(null);
    setStartTime(null);
  };

  const selectShop = (next: Shop) => {
    if (next.id === shop?.id) return;
    setShop(next);
    resetServiceChoices();
  };

  const selectDate = (next: string) => {
    if (next === date) return;
    setDate(next);
    resetServiceChoices();
  };

  /**
   * ◀ ▶ trên timeline (bước 2): lướt ngày để xem lịch nhưng GIỮ course/add-on
   * đã chọn — chỉ giờ và chỉ định đích danh (phụ thuộc ca ngày đó) phải bỏ.
   */
  const browseDate = (next: string) => {
    if (next === date) return;
    setDate(next);
    setStartTime(null);
    setTherapist(null);
  };

  const selectPartySize = (next: PartySize) => {
    if (next === partySize) return;
    setPartySize(next);
    // Giữ course đã chọn — chỉ add-on theo từng người và giờ là phải tính lại.
    setGuestAddons(emptyAddons(next));
    setStartTime(null);
    // BR-04: nhóm từ 2 người không được chỉ định nhân viên.
    if (next >= 2) {
      setTherapistGender(null);
      setTherapist(null);
    }
  };

  /** Chọn giới tính thì bỏ chỉ định đích danh — BE cấm gửi cả hai. */
  const selectTherapistGender = (next: Gender | null) => {
    setTherapistGender(next);
    setTherapist(null);
    setStartTime(null);
  };

  const selectTherapist = (next: Therapist | null) => {
    setTherapist(next);
    setTherapistGender(null);
    setStartTime(null);
  };

  const selectCourse = (next: number) => {
    if (next === courseId) return;
    setCourseId(next);
    setGuestAddons(emptyAddons(partySize));
    setStartTime(null);
  };

  const restart = () => {
    setStep(1);
    setShop(null);
    setDate(null);
    setPartySize(1);
    setCourseId(null);
    setGuestAddons(emptyAddons(1));
    setTherapistGender(null);
    setTherapist(null);
    setStartTime(null);
    setPhone("");
    setEmail("");
    setCustomer(null);
    setCreated(null);
  };

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
      {/* Thanh điều hướng bước — hộp ôm sát dãy bước, căn giữa khung. Viền nhẹ
          (không dùng khung cửa sổ .wf) vì kéo hết bề ngang sẽ thành hộp rỗng. */}
      <div className="mb-5 flex justify-center">
        <div className="inline-flex rounded-md border border-line-strong bg-surface-2 px-3 py-2">
          <Stepper current={step} onNavigate={setStep} />
        </div>
      </div>

      {step === 1 ? (
        <StepWindow step={1}>
          <StepShopDate
            shops={shops}
            services={services}
            shop={shop}
            date={date}
            partySize={partySize}
            closedDates={closedDates}
            onSelectShop={selectShop}
            onSelectDate={selectDate}
            onSelectPartySize={selectPartySize}
            onNext={() => setStep(2)}
          />
        </StepWindow>
      ) : null}

      {/* Wireframe 02: "timeline + form đặt chỗ 1 trang" — một cửa sổ liền mạch,
          bên trong chia hai mục Lịch slot / Form đặt chỗ. */}
      {step === 2 && shop && date ? (
        <StepWindow step={2}>
          <StepServices
            shop={shop}
            date={date}
            partySize={partySize}
            services={services}
            courseId={courseId}
            guestAddons={guestAddons}
            therapistGender={therapistGender}
            therapist={therapist}
            startTime={startTime}
            onSelectCourse={selectCourse}
            onChangeGuestAddons={setGuestAddons}
            onSelectTherapistGender={selectTherapistGender}
            onSelectTherapist={selectTherapist}
            onSelectStartTime={setStartTime}
            onSelectDate={browseDate}
            onBack={() => setStep(1)}
            onNext={() => setStep(3)}
          />
        </StepWindow>
      ) : null}

      {step === 3 && shop ? (
        <StepWindow step={3}>
          <StepCustomer
            phone={phone}
            email={email}
            customer={customer}
            onChangePhone={(next) => {
              setPhone(next);
              setCustomer(null);
            }}
            onChangeEmail={setEmail}
            onLookupResult={setCustomer}
            onBack={() => setStep(2)}
            onNext={() => setStep(4)}
          />
        </StepWindow>
      ) : null}

      {step === 4 && shop && date && startTime && courseId && services.data ? (
        <StepWindow step={4}>
          <StepConfirm
            shop={shop}
            date={date}
            startTime={startTime}
            partySize={partySize}
            courseId={courseId}
            guestAddons={guestAddons}
            therapistGender={therapistGender}
            therapist={therapist}
            phone={phone}
            email={email}
            services={services.data}
            onPickSuggestedSlot={setStartTime}
            onEditStep={setStep}
            onBack={() => setStep(3)}
            onCreated={(booking) => {
              setCreated(booking);
              setStep(5);
            }}
          />
        </StepWindow>
      ) : null}

      {step === 5 && created ? (
        <StepWindow step={5}>
          <StepSuccess booking={created} onRestart={restart} />
        </StepWindow>
      ) : null}
    </div>
  );
}
