import type { Metadata } from "next";
import { Itim, Geist_Mono } from "next/font/google";
import "./globals.css";

// Wireframe dùng Itim (viết tay, có tiếng Việt) — Itim chỉ có weight 400.
const itim = Itim({
  variable: "--font-itim",
  weight: "400",
  subsets: ["vietnamese", "latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Đặt lịch massage online",
  description: "Đặt lịch massage: chọn cửa hàng, dịch vụ và giờ hẹn.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="vi"
      className={`${itim.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans">{children}</body>
    </html>
  );
}
