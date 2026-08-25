/** Display helpers for Status Pengiriman progress cells. */

export function clampPercent(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return 0;
  return Math.min(100, Math.round(v));
}

/** Terkirim tone: empty / low orange / mid-high yellow / full green. */
export function terkirimTone(pct) {
  const p = clampPercent(pct);
  if (p <= 0) return "empty";
  if (p >= 100) return "full";
  if (p < 50) return "low";
  return "mid";
}

export function formatShipDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString("id-ID", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    });
  } catch {
    return "-";
  }
}
