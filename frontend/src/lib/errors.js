// Task 14 bugfix: normalize backend HTTPException `detail` — sometimes string,
// sometimes object `{message, prereq_reasons, ...}`, sometimes FastAPI validation array.
// Prevents React crash 'Objects are not valid as a React child' when the detail is set into state
// and rendered directly.
export function formatApiError(err, fallback = "Terjadi kesalahan") {
  const d = err?.response?.data?.detail;
  if (!d) return err?.message || fallback;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) {
    const msgs = d
      .map((item) => (typeof item === "string" ? item : item?.msg || item?.message))
      .filter(Boolean);
    if (msgs.length) return msgs.join("; ");
    return fallback;
  }
  if (typeof d === "object") {
    const parts = [];
    if (d.message) parts.push(d.message);
    if (Array.isArray(d.prereq_reasons) && d.prereq_reasons.length) {
      parts.push(...d.prereq_reasons.map((r) => `• ${r}`));
    }
    if (parts.length) return parts.join("\n");
    try { return JSON.stringify(d); } catch (_) { return fallback; }
  }
  return String(d);
}
