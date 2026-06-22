import { createClient, type SupabaseClient } from '@supabase/supabase-js'

// Anon key ONLY (public by design — safe in the browser). Used in human mode to
// read masked staff messages (messages_public) and watch the conversation mode
// flip back to 'agent'. NEVER the service role. null when unconfigured → the
// widget polls messages_public instead.
const url = import.meta.env.VITE_SUPABASE_URL
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY

export const supabase: SupabaseClient | null =
  url && anon
    ? createClient(url, anon, { auth: { persistSession: false, autoRefreshToken: false } })
    : null

export const realtimeEnabled = supabase !== null
