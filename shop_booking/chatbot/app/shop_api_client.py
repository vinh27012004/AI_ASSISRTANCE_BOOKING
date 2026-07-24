"""api_client — gọi endpoint GĐ1 của shop_api (DD §2.2/§4). CHỈ code gọi, LLM không gọi.

Chatbot dùng bộ API GĐ1 như một client PUBLIC (giống FE web) — không cần auth kênh riêng
(mentor: chỉ gọi API để lấy/ghi thông tin). Lỗi nghiệp vụ trả về nguyên envelope -> bọc
thành ShopApiError để state machine map nhánh (§3.6). Không định nghĩa lại kiểu — trích
thẳng openapi.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional


class ShopApiError(Exception):
    """Lỗi nghiệp vụ/hạ tầng từ shop_api (envelope {error:{code,message,details}})."""

    def __init__(self, status: int, code: str, message: str, details: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}


class ShopApiClient:
    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # --- HTTP core ---
    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None, extra_headers: dict | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)

        headers = {"Accept": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raise self._to_api_error(e)
        except (urllib.error.URLError, TimeoutError) as e:
            raise ShopApiError(503, "INTERNAL_ERROR", f"Không gọi được shop_api: {e}")

    @staticmethod
    def _to_api_error(e: "urllib.error.HTTPError") -> ShopApiError:
        try:
            payload = json.loads(e.read().decode("utf-8"))
            err = payload.get("error", {})
            return ShopApiError(e.code, err.get("code", "INTERNAL_ERROR"),
                                err.get("message", "Lỗi hệ thống."), err.get("details"))
        except (ValueError, AttributeError):
            return ShopApiError(e.code, "INTERNAL_ERROR", "Lỗi hệ thống.")

    # --- Endpoints (DD §2.2) ---
    def get_shops(self) -> list[dict]:
        return self._request("GET", "/shops")

    def get_services(self, shop_id: int, date: str, party_size: int | None = None) -> dict:
        return self._request("GET", f"/shops/{shop_id}/services",
                             params={"date": date, "party_size": party_size})

    def get_slots(self, shop_id: int, *, date: str, party_size: int, course_id: int,
                  addon_ids: list[int] | None = None, therapist_id: int | None = None,
                  therapist_gender: str | None = None) -> dict:
        return self._request("GET", f"/shops/{shop_id}/slots", params={
            "date": date, "party_size": party_size, "course_id": course_id,
            "addon_ids": ",".join(str(a) for a in (addon_ids or [])) or None,
            "therapist_id": therapist_id, "therapist_gender": therapist_gender,
        })

    def get_therapists(self, shop_id: int, date: str) -> dict:
        return self._request("GET", f"/shops/{shop_id}/therapists", params={"date": date})

    def lookup_customer(self, phone: str) -> dict:
        return self._request("POST", "/customers/lookup", body={"phone": phone})

    def create_booking(self, body: dict) -> dict:
        # BE chống bấm đúp bằng dedup thời gian 120s (cùng khách+shop+ngày+giờ).
        return self._request("POST", "/bookings", body=body)

    # --- Sửa/hủy trong phiên (DD §2.2) ---
    def patch_booking(self, booking_code: str, body: dict, edit_token: str | None = None) -> dict:
        # ≤2 phút: X-Edit-Token (BR-17). Hết hạn: bỏ header, xác thực bằng email trong body
        # (BR-15) — verify_booking_access ưu tiên token, nên gửi token đã hết hạn sẽ 401.
        headers = {"X-Edit-Token": edit_token} if edit_token else None
        return self._request("PATCH", f"/bookings/{booking_code}", body=body,
                             extra_headers=headers)

    def cancel_booking(self, booking_code: str, email: str) -> dict:
        return self._request("POST", f"/bookings/{booking_code}/cancel", body={"email": email})

    def retrieve_booking(self, booking_code: str, email: str) -> dict:
        return self._request("POST", "/bookings/retrieve",
                             body={"booking_code": booking_code, "email": email})
