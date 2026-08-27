export function fmtQty(n: number | null | undefined): string {
  const v = Number(n || 0);
  return v.toLocaleString("id-ID");
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const s = String(value).trim();
  if (!s) return "—";
  // Already date-only YYYY-MM-DD
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
    const [y, m, d] = s.split("-");
    return `${d}/${m}/${y}`;
  }
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleDateString("id-ID", { day: "2-digit", month: "2-digit", year: "numeric" });
}

export function newActionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function apiErrorMessage(err: unknown, fallback = "Terjadi kesalahan"): string {
  const e = err as { message?: string; detail?: unknown; status?: number };
  const detail = e?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object" && "message" in (detail as object)) {
    const m = (detail as { message?: string }).message;
    if (m) return m;
  }
  if (e?.message) return e.message;
  return fallback;
}
