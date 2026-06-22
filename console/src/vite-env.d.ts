/// <reference types="vite/client" />

interface ImportMetaEnv {
  // Absolute agent base URL. Leave EMPTY in dev → console calls relative
  // /staff,/chat and Vite proxies them to the agent (no CORS, no hardcoded host).
  readonly VITE_AGENT_URL?: string;
  // Optional dev convenience: pre-fill the login token (login screen overrides).
  readonly VITE_STAFF_TOKEN?: string;
  // Supabase ANON key only (public by design) — Realtime push + messages_public.
  readonly VITE_SUPABASE_URL?: string;
  readonly VITE_SUPABASE_ANON_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
