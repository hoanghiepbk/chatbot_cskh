import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";

// TIP-014: dev proxy so the console calls relative /staff and /chat and Vite
// forwards them to the agent. Keeps the browser bundle free of a hardcoded host
// and avoids CORS in dev — sensitive data still flows only through the agent
// staff API (Bearer), never Supabase service_role on the frontend.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const agentTarget = env.VITE_AGENT_PROXY_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [react()],
    server: {
      proxy: {
        "/staff": { target: agentTarget, changeOrigin: true },
        "/chat": { target: agentTarget, changeOrigin: true },
      },
    },
  };
});
