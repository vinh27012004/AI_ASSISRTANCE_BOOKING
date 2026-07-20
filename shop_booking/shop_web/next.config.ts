import type { NextConfig } from "next";

// Flask (shop_api) chạy riêng ở cổng 5000. Rewrite để trình duyệt chỉ gọi
// same-origin `/api/v1/...` — không cần bật CORS ở BE.
const apiTarget = process.env.API_TARGET ?? "http://127.0.0.1:5000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiTarget}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
