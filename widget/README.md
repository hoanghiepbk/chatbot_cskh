# XeCare Widget (TIP-014w)

Customer-facing chat widget for the XeCare agent — a friendly storefront (NOT the
operator console). Phone gate → multi-turn chat with SSE streaming, citation
chips, a confirm card for bookings/cancels, an emergency hotline banner, and
human-takeover mode. Built on Vite + React + TS (no UI framework).

## Run (dev)

1. Start the agent (port 8000) against a real Supabase, with `ANTHROPIC_API_KEY`
   and `PHONE_HASH_SALT` set (and seed data for personalized greetings).
2. `cp .env.example .env` and fill in (all optional for a basic run):
   - `VITE_AGENT_PROXY_TARGET` — agent URL the Vite proxy forwards `/chat`
     (incl. the SSE stream) to. Leave `VITE_AGENT_URL` empty in dev.
   - `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` — enable human-mode Realtime
     (staff messages + mode flips). Without them the widget polls
     messages_public every 3s; agent-mode chat works without it.
3. `npm install && npm run dev` → open the URL, enter a seeded phone (e.g.
   `+84901000001`).

## Scripts

- `npm run dev` — Vite dev server (with `/chat` proxy + SSE).
- `npm run build` — type-check (`tsc -b`) + production bundle.
- `npm run lint` — ESLint.

## Security / privacy notes

- `conversation_id` lives ONLY in React state — never localStorage/sessionStorage.
  A refresh starts a fresh session (accepted for the demo).
- Supabase is used with the **anon key only** (public) for human-mode reads; the
  service role never reaches the browser. Sensitive flows go through the agent API.
- Every message (incl. the agent's own reply) renders through `<PlainText>`
  (HTML-escaped) — no `dangerouslySetInnerHTML`, no markdown renderer.

Visual VERIFY (4 reply types, responsive on a real phone) is scheduled for
TIP-016 per the project plan; this TIP self-checks logic + build only.
