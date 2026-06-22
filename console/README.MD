# XeCare Console (TIP-014)

Internal **operator tool** for the XeCare agent: Trace Explorer, Ops Dashboard,
Eval Dashboard, and the HITL queue. Built on Refine + AntD + Vite. It reads
sensitive data **only** through the agent staff API (Bearer `STAFF_API_TOKEN`)
and uses the Supabase **anon** key just for Realtime push + masked
`messages_public` reads — never the service role.

## Run (dev)

1. Start the agent (port 8000) against a real Supabase, with `STAFF_API_TOKEN` set.
2. `cp .env.example .env` and fill in (all optional for a basic run):
   - `VITE_AGENT_PROXY_TARGET` — agent URL the Vite proxy forwards `/staff` and
     `/chat` to (default `http://127.0.0.1:8000`). Leave `VITE_AGENT_URL` empty
     in dev so the bundle has no hardcoded host and there's no CORS.
   - `VITE_SUPABASE_URL` + `VITE_SUPABASE_ANON_KEY` — enable Realtime push and
     live-chat history. Without them the console polls the staff API (every 5s)
     and the live-chat history panel is disabled.
3. `npm install && npm run dev` → open the URL, paste the `STAFF_API_TOKEN`.

A wrong token → the login form shows a clear 401 error.

## Scripts

- `npm run dev` — Vite dev server (with `/staff`,`/chat` proxy to the agent).
- `npm run build` — type-check (`tsc`) + production bundle.

## Demo data

The dashboards need conversations / trace / eval rows to be interesting. Run a
few chats through the agent API first (`/chat/start` → `/chat/{id}/message`)
and, optionally, `evals/runner.py` to populate `eval_runs`. Every screen has an
action-oriented empty state when there's no data yet.

## Security notes (demo-grade)

- Token lives in `sessionStorage` (dies with the tab, never on disk). It is a
  single shared bearer with full staff power incl. PII reveal — production must
  move to Supabase Auth + per-staff identity + RLS (see `agent/app/api/staff.py`
  threat-model note).
- All customer/agent/trace text renders through `<PlainText>` (HTML-escaped) —
  no `dangerouslySetInnerHTML`, no markdown renderer on untrusted content
  (TIP-007 anti-injection decision, aimed at the operator).
- PII is revealed only via the audited `reveal_contact` button (logs a
  `pii_reveal` trace); the real number is never shown by default.
