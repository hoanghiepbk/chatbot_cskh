import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// TIP-014w: dev proxy so the widget calls relative /chat (incl. the SSE
// /message_stream) and Vite forwards to the agent — no CORS, no hardcoded host.
// Realtime (human-mode) talks to Supabase directly via the anon key.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const agentTarget = env.VITE_AGENT_PROXY_TARGET || 'http://127.0.0.1:8000'
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/chat': { target: agentTarget, changeOrigin: true },
      },
    },
  }
})
