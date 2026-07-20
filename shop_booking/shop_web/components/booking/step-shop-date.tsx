"use client";

import { useState } from "react";
import { Alert, Chip, Field, LoadingLine } from "@/components/ui";
import type { RequestState } from "@/lib/use-request";
import type { ServicesResponse, Shop } from "@/lib/types";
import { Calendar } from "./calendar";
import { StepFooter } from "./step-footer";
import type { PartySize } from "./booking-wizard";

const PARTY_SIZES: PartySize[] = [1, 2, 3];

/**
 * BR-14 chặn ở FE cho UX (BE vẫn validate lại). Đây là chỗ duy nhất FE phải tự
 * giữ text lỗi, vì khách chưa hề gọi API — text copy nguyên văn từ catalog lỗi.
 */
const PARTY_SIZE_EXCEEDED_MESSAGE =
  "Mỗi lượt đặt tối đa 3 người. Nhóm đông hơn vui lòng liên hệ trực tiếp cửa hàng.";

export function StepShopDate({
  shops,
  services,
  shop,
  date,
  partySize,
  closedDates,
  onSelectShop,
  onSelectDate,
  onSelectPartySize,
  onNext,
}: {
  shops: RequestState<Shop[]>;
  services: RequestState<ServicesResponse>;
  shop: Shop | null;
  date: string | null;
  partySize: PartySize;
  closedDates: ReadonlySet<string>;
  onSelectShop: (shop: Shop) => void;
  onSelectDate: (iso: string) => void;
  onSelectPartySize: (size: PartySize) => void;
  onNext: () => void;
}) {
  const [showPartyLimit, setShowPartyLimit] = useState(false);

  const shopClosed = services.data?.reason === "SHOP_CLOSED";
  const canContinue =
    Boolean(shop && date) && !services.loading && !services.error && !shopClosed;

  return (
    <>
      <Field label="Cửa hàng">
        {shops.loading ? <LoadingLine label="Đang tải danh sách cửa hàng…" /> : null}

        {shops.error ? (
          <Alert tone="danger">{shops.error.message}</Alert>
        ) : null}

        {shops.data ? (
          shops.data.length === 0 ? (
            <p className="text-sm text-ink-2">Chưa có cửa hàng nào.</p>
          ) : (
            <>
              <label className="sr-only" htmlFor="shop-select">
                Chọn cửa hàng
              </label>
              <select
                id="shop-select"
                value={shop?.id ?? ""}
                onChange={(event) => {
                  const next = shops.data?.find(
                    (item) => item.id === Number(event.target.value),
                  );
                  if (next) onSelectShop(next);
                }}
                className="w-full max-w-sm rounded border border-line-strong bg-surface px-2.5 py-1.5 text-sm text-ink transition-colors hover:border-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
              >
                <option value="" disabled>
                  — Chọn cửa hàng —
                </option>
                {shops.data.map((item) => (
                  <option key={item.id} value={item.id}>
                    【{item.shop_code}】{item.name}
                  </option>
                ))}
              </select>

              {shop ? (
                <p className="mt-2 text-xs text-ink-3">
                  {shop.address} · ☎ {shop.phone}
                </p>
              ) : null}
            </>
          )
        ) : null}
      </Field>

      <Field label="Ngày">
        <Calendar
          value={date}
          onChange={onSelectDate}
          closedDates={closedDates}
        />
      </Field>

      <Field label="Số người">
        <div className="flex flex-wrap items-center gap-2">
          {PARTY_SIZES.map((size) => (
            <Chip
              key={size}
              selected={partySize === size}
              onClick={() => {
                setShowPartyLimit(false);
                onSelectPartySize(size);
              }}
              className="min-w-11"
            >
              {size}
            </Chip>
          ))}
          <Chip
            selected={false}
            onClick={() => setShowPartyLimit(true)}
            className="min-w-11"
          >
            4+
          </Chip>
        </div>

        {showPartyLimit ? (
          <Alert tone="warn" className="mt-3" title="Nhóm trên 3 người">
            {PARTY_SIZE_EXCEEDED_MESSAGE}
            {shop ? <> ☎ {shop.phone}</> : null}
          </Alert>
        ) : null}
      </Field>

      {shop && date ? (
        <div className="px-4 py-3">
          {services.loading ? (
            <LoadingLine label="Đang kiểm tra dịch vụ của ngày này…" />
          ) : null}

          {services.error ? (
            <Alert tone="danger">{services.error.message}</Alert>
          ) : null}

          {/* Case A1 — ngày shop nghỉ / thiếu nhân viên */}
          {shopClosed ? (
            <Alert tone="warn">
              Cửa hàng không phục vụ ngày này, vui lòng chọn ngày khác.
            </Alert>
          ) : null}
        </div>
      ) : null}

      <StepFooter onNext={onNext} nextDisabled={!canContinue} />
    </>
  );
}
