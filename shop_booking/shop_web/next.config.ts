import type { NextConfig } from "next";

// Flask (shop_api) chạy riêng ở cổng 5000. Rewrite để trình duyệt chỉ gọi
// same-origin `/api/v1/...` — không cần bật CORS ở BE.
const apiTarget = process.env.API_TARGET ?? "http://127.0.0.1:5000";

// Chatbot service (GĐ2) chạy riêng ở cổng 5100, endpoint `/chat/message`.
// Cùng nguyên tắc same-origin như shop_api: widget gọi `/api/chat/...`, rewrite
// chuyển sang service — trình duyệt không phải bật CORS.
const chatTarget = process.env.CHAT_API_TARGET ?? "http://127.0.0.1:5100";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/v1/:path*",
        destination: `${apiTarget}/api/v1/:path*`,
      },
      {
        source: "/api/chat/:path*",
        destination: `${chatTarget}/chat/:path*`,
      },
    ];
  },
};

export default nextConfig;
