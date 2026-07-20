"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, isAbort, toApiError } from "./api";

export type RequestState<T> = {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
};

type Result<T> = { stamp: string; data: T | null; error: ApiError | null };

/**
 * Gọi API mỗi khi `key` đổi. `key === null` = chưa đủ điều kiện, không gọi.
 *
 * `loading` được suy ra lúc render bằng cách so `key` hiện tại với key của kết
 * quả đang giữ, nên không cần setState đồng bộ trong effect. Kết quả về muộn
 * của key cũ bị bỏ qua (stamp không khớp) → đổi lựa chọn nhanh không gây race.
 */
export function useRequest<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
): RequestState<T> & { reload: () => void } {
  const [nonce, setNonce] = useState(0);
  const [result, setResult] = useState<Result<T> | null>(null);

  const stamp = key === null ? null : `${nonce}#${key}`;

  useEffect(() => {
    if (stamp === null) return;

    const controller = new AbortController();
    let cancelled = false;

    fetcher(controller.signal).then(
      (data) => {
        if (!cancelled) setResult({ stamp, data, error: null });
      },
      (error: unknown) => {
        if (cancelled || isAbort(error)) return;
        setResult({ stamp, data: null, error: toApiError(error) });
      },
    );

    return () => {
      cancelled = true;
      controller.abort();
    };
    // `fetcher` cố ý không nằm trong deps: nó là closure mới mỗi lần render,
    // nhưng closure được dùng luôn là của render mà `stamp` đổi — tức là đã
    // đọc đúng state mới nhất.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stamp]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  const fresh = result !== null && result.stamp === stamp;

  return {
    data: fresh ? result.data : null,
    error: fresh ? result.error : null,
    loading: stamp !== null && !fresh,
    reload,
  };
}
