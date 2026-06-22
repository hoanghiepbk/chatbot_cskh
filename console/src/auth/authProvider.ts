import type { AuthProvider } from "@refinedev/core";
import { pingToken } from "../api/client";
import { clearToken, getToken, setToken } from "./token";

// Refine auth wired to the shared STAFF_API_TOKEN. login() validates the token
// against a cheap staff endpoint before storing it, so a wrong token surfaces a
// clear error on the login form (acceptance: sai token → 401 → báo lỗi rõ).
export const authProvider: AuthProvider = {
  login: async ({ token }: { token?: string }) => {
    if (!token) {
      return { success: false, error: { name: "Thiếu token", message: "Vui lòng nhập token" } };
    }
    const ok = await pingToken(token);
    if (!ok) {
      return {
        success: false,
        error: { name: "Sai token", message: "Token nhân viên không hợp lệ (401)" },
      };
    }
    setToken(token);
    return { success: true, redirectTo: "/" };
  },
  logout: async () => {
    clearToken();
    return { success: true, redirectTo: "/login" };
  },
  check: async () => {
    if (getToken()) return { authenticated: true };
    return {
      authenticated: false,
      redirectTo: "/login",
      error: { name: "Chưa đăng nhập", message: "Cần token nhân viên" },
    };
  },
  onError: async (error) => {
    if (error?.status === 401) {
      clearToken();
      return { logout: true, redirectTo: "/login" };
    }
    return {};
  },
  getIdentity: async () => {
    if (!getToken()) return null;
    return { id: "staff", name: "Nhân viên trực" };
  },
};
