"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api, authStorage, toApiError } from "@/lib/api";
import { Card, Button, TextInput, Alert, Spinner } from "@/components/ui";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  useEffect(() => {
    // If already logged in, redirect accordingly
    const user = authStorage.getUser();
    if (user) {
      if (user.role === "admin") {
        router.push("/admin/bookings");
      } else if (user.role === "therapist") {
        router.push("/therapist/schedule");
      }
    }
  }, [router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const res = await api.login({ username, password });
      authStorage.setToken(res.access_token);
      authStorage.setUser(res.role, res.therapist_id);
      
      if (res.role === "admin") {
        router.push("/admin/bookings");
      } else if (res.role === "therapist") {
        router.push("/therapist/schedule");
      }
    } catch (err) {
      setError(toApiError(err).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4 py-12 sm:px-6 lg:px-8">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center">
          <h2 className="text-3xl font-extrabold tracking-tight text-ink">
            Đăng nhập hệ thống
          </h2>
          <p className="mt-2 text-sm text-ink-2">
            Dành cho Quản trị viên và Nhân viên trị liệu
          </p>
        </div>

        <Card className="p-6 sm:p-8">
          <form onSubmit={handleSubmit} className="space-y-6">
            {error && <Alert tone="danger">{error}</Alert>}

            <div>
              <label
                htmlFor="username"
                className="block text-sm font-medium text-ink-2 mb-2"
              >
                Tên đăng nhập
              </label>
              <TextInput
                id="username"
                type="text"
                required
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                placeholder="Nhập tên đăng nhập"
                disabled={loading}
              />
            </div>

            <div>
              <label
                htmlFor="password"
                className="block text-sm font-medium text-ink-2 mb-2"
              >
                Mật khẩu
              </label>
              <TextInput
                id="password"
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Nhập mật khẩu"
                disabled={loading}
              />
            </div>

            <Button
              type="submit"
              variant="primary"
              className="w-full justify-center"
              disabled={loading}
            >
              {loading ? (
                <>
                  <Spinner className="mr-2" />
                  Đang đăng nhập...
                </>
              ) : (
                "Đăng nhập"
              )}
            </Button>
          </form>
        </Card>

        <p className="text-center text-sm">
          <Link
            href="/"
            className="text-ink-3 underline underline-offset-2 hover:text-ink-2"
          >
            ← Về trang đặt lịch
          </Link>
        </p>
      </div>
    </div>
  );
}