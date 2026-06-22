interface ImportMetaEnv {
  // Absolute agent base. EMPTY in dev → relative /chat via the Vite proxy.
  readonly VITE_AGENT_URL?: string
  // Supabase ANON key only (public) — human-mode Realtime + messages_public.
  readonly VITE_SUPABASE_URL?: string
  readonly VITE_SUPABASE_ANON_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
