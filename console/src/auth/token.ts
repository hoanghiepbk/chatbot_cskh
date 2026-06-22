// Staff token store. THREAT MODEL (demo-grade, intentional): a single shared
// STAFF_API_TOKEN grants full staff power incl. PII reveal. We keep it in
// sessionStorage (NOT localStorage) so it dies when the tab closes and is never
// persisted to disk. Production must replace this with Supabase Auth + per-staff
// identity + RLS (see agent/app/api/staff.py threat-model note).

const KEY = "xecare_staff_token";

export function getToken(): string | null {
  return sessionStorage.getItem(KEY) ?? import.meta.env.VITE_STAFF_TOKEN ?? null;
}

export function setToken(token: string): void {
  sessionStorage.setItem(KEY, token);
}

export function clearToken(): void {
  sessionStorage.removeItem(KEY);
}

export function hasToken(): boolean {
  return Boolean(getToken());
}
