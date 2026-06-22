import { clearToken, getToken } from "../auth/token";

// EMPTY base in dev → relative /staff calls go through the Vite proxy. An
// absolute VITE_AGENT_URL targets the agent directly (production).
const BASE = import.meta.env.VITE_AGENT_URL ?? "";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  });

  if (res.status === 401) {
    // Session token revoked/expired mid-use → drop it and ask the app to bounce
    // to /login. (The login validation path uses pingToken, not this, so a bad
    // login never triggers this branch.)
    clearToken();
    window.dispatchEvent(new CustomEvent("xecare:unauthorized"));
    throw new ApiError(401, "Phiên đăng nhập hết hạn hoặc token không hợp lệ");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = (body?.detail as string) ?? detail;
    } catch {
      // non-JSON error body — keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      body: body !== undefined ? JSON.stringify(body) : undefined,
    }),
};

// Validate a candidate token WITHOUT touching the stored session (used at login).
export async function pingToken(token: string): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/staff/eval-runs?limit=1`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    return res.ok;
  } catch {
    return false;
  }
}
