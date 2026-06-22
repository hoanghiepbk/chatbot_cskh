import type { ThemeConfig } from "antd";

// TIP-014 DESIGN DIRECTION — internal operator tool, NOT a landing page.
// Light neutral surface, technical-blue accent, semantic status colors, and a
// monospace stack for data/numbers/trace ids (the "real engineering tool" tell).
// Deliberately NOT cream-terracotta or black-acid-green AI templates.

export const MONO =
  "'JetBrains Mono', ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace";

export const SEMANTIC = {
  pass: "#16A34A", // resolved / pass
  warn: "#D97706", // rewrite / warning
  fail: "#DC2626", // block / fail / urgent
  accent: "#2563EB", // primary action
  ink: "#1A1F2B",
  surface: "#FFFFFF",
  bg: "#F7F8FA",
  border: "#E5E7EB",
  muted: "#6B7280",
};

export const consoleTheme: ThemeConfig = {
  token: {
    colorPrimary: SEMANTIC.accent,
    colorSuccess: SEMANTIC.pass,
    colorWarning: SEMANTIC.warn,
    colorError: SEMANTIC.fail,
    colorTextBase: SEMANTIC.ink,
    colorBgLayout: SEMANTIC.bg,
    colorBorderSecondary: SEMANTIC.border,
    fontFamily:
      "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif",
    borderRadius: 6,
    fontSize: 14,
  },
  components: {
    // Dense tables — operator screens prioritize information density.
    Table: { cellPaddingBlockSM: 6, cellPaddingInlineSM: 10, headerBg: "#F1F3F5" },
    Layout: { bodyBg: SEMANTIC.bg, headerBg: SEMANTIC.surface, siderBg: SEMANTIC.surface },
    Menu: { itemBg: SEMANTIC.surface },
    Card: { paddingLG: 16 },
  },
};
