// Display formatters. Numbers/ids/latency render in monospace at call sites.

export function fmtUsd(v?: number | string | null, digits = 4): string {
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `$${n.toFixed(digits)}`;
}

export function fmtMs(v?: number | null): string {
  if (v === null || v === undefined) return "—";
  return `${Math.round(v)} ms`;
}

export function fmtPct(v?: number | null, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return `${(v * 100).toFixed(digits)}%`;
}

export function shortId(id?: string | null): string {
  if (!id) return "—";
  return id.length > 8 ? id.slice(0, 8) : id;
}

export function fmtTime(ts?: string | null): string {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString("vi-VN", { hour12: false });
}

export function fmtDay(ts?: string | null): string {
  if (!ts) return "—";
  return ts.slice(0, 10);
}
