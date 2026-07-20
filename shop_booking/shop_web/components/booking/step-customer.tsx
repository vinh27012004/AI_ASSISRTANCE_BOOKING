"use client";

import { useState, type FormEvent } from "react";
import { ApiError, api, toApiError } from "@/lib/api";
import type { CustomerInfo } from "@/lib/types";
import { Alert, Button, Field, Spinner, TextInput } from "@/components/ui";
import { StepFooter } from "./step-footer";

/** Khớp regex BE: ^\d{8,15}$ */
const PHONE_PATTERN = /^\d{8,15}$/;
/** Khớp regex BE: [^@]+@[^@]+\.[^@]+ */
const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export function StepCustomer({
  phone,
  email,
  customer,
  onChangePhone,
  onChangeEmail,
  onLookupResult,
  onBack,
  onNext,
}: {
  phone: string;
  email: string;
  customer: CustomerInfo | null;
  onChangePhone: (next: string) => void;
  onChangeEmail: (next: string) => void;
  onLookupResult: (info: CustomerInfo) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const phoneValid = PHONE_PATTERN.test(phone);
  const emailValid = EMAIL_PATTERN.test(email);
  const blocked = error?.code === "PHONE_BLOCKED";

  const lookup = async (event: FormEvent) => {
    event.preventDefault();
    if (!phoneValid || loading) return;

    setLoading(true);
    setError(null);
    try {
      onLookupResult(await api.lookupCustomer(phone));
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setLoading(false);
    }
  };

  const changePhone = (next: string) => {
    // BE chỉ nhận chữ số (không dấu "-"), lọc luôn khi gõ.
    onChangePhone(next.replace(/\D/g, ""));
    setError(null);
  };

  return (
    <>
      <Field label="Số điện thoại" hint="Chỉ nhập số, không có dấu “-”">
        {/* Cùng max-w-md với ô Email bên dưới để hai ô thẳng mép phải — SĐT chỉ
            8–15 chữ số, kéo dài hết hàng vừa xấu vừa vô ích. */}
        <form onSubmit={lookup} className="flex max-w-md flex-wrap items-start gap-2">
          <div className="min-w-0 flex-1">
            <TextInput
              value={phone}
              onChange={(event) => changePhone(event.target.value)}
              inputMode="numeric"
              autoComplete="tel"
              placeholder="09012345678"
              aria-label="Số điện thoại"
              invalid={Boolean(phone) && !phoneValid}
            />
            {phone && !phoneValid ? (
              <p className="mt-1.5 text-xs text-danger">
                Số điện thoại phải có 8–15 chữ số.
              </p>
            ) : null}
          </div>
          {/* Nút xanh viền .box.a như wireframe 03 */}
          <Button type="submit" variant="accent" disabled={!phoneValid || loading}>
            {loading ? <Spinner /> : null}
            Kiểm tra
          </Button>
        </form>

        {/* Case A5 — SĐT trong NG list (BR-06), hiện kèm lý do (BR-20) */}
        {error ? (
          <Alert tone="danger" className="mt-3">
            {error.message}
            {blocked && typeof error.details?.reason === "string" ? (
              <div className="mt-1.5 text-xs opacity-90">
                Lý do: {error.details.reason}
                {typeof error.details?.shop_phone === "string" ? (
                  <> · ☎ {error.details.shop_phone}</>
                ) : null}
              </div>
            ) : null}
          </Alert>
        ) : null}

        {customer ? (
          customer.member_type === "member" ? (
            <Alert tone="info" className="mt-3" title="👤 Chào mừng quay lại!">
              Thành viên
              {customer.rank ? (
                <>
                  {" "}
                  · Hạng <b>{customer.rank}</b>
                </>
              ) : null}{" "}
              · Đã ghé <b>{customer.visit_count}</b> lần
            </Alert>
          ) : (
            <Alert tone="success" className="mt-3" title="Khách mới — chào mừng bạn!">
              Chỉ cần số điện thoại và email là đặt được.
            </Alert>
          )
        ) : null}
      </Field>

      <Field
        label="Email"
        hint="Mã đặt chỗ gửi qua email, dùng để sửa hoặc hủy sau này"
      >
        <div className="max-w-md">
          <TextInput
            type="email"
            value={email}
            onChange={(event) => onChangeEmail(event.target.value)}
            autoComplete="email"
            placeholder="ban@vidu.com"
            aria-label="Email"
            invalid={Boolean(email) && !emailValid}
          />
          {email && !emailValid ? (
            <p className="mt-1.5 text-xs text-danger">Email chưa đúng định dạng.</p>
          ) : null}
        </div>
      </Field>

      <StepFooter
        onBack={onBack}
        onNext={onNext}
        nextDisabled={!customer || !emailValid || blocked}
      >
        {!customer && !blocked ? (
          <p className="text-xs text-ink-3">
            Bấm “Kiểm tra” để tiếp tục.
          </p>
        ) : null}
      </StepFooter>
    </>
  );
}
