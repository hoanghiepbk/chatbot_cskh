import { createClient, type SupabaseClient } from "@supabase/supabase-js";

// Anon key ONLY (public by design — safe in the browser). Used for:
//   - Realtime push: `conversations` is anon-readable via RLS, so mode flips
//     (claim → human, resolve → agent) and new conversations push live.
//   - messages_public reads: masked content_masked rows for the live chat.
// NEVER the service role — all sensitive data (tickets, trace, metrics, reveal)
// flows through the agent staff API with a Bearer token. null when unconfigured,
// in which case callers degrade to staff-API polling.
const url = import.meta.env.VITE_SUPABASE_URL;
const anon = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase: SupabaseClient | null =
  url && anon
    ? createClient(url, anon, {
        auth: { persistSession: false, autoRefreshToken: false },
      })
    : null;

export const realtimeEnabled = supabase !== null;
