/** Armada Qty SJ capacity — display/parse helpers. Stored value is a positive integer. */

export function formatArmadaCapacityQty(n) {
  if (n == null || n === "") return "";
  const v = Number(n);
  if (!Number.isFinite(v)) return "";
  return Math.trunc(v).toLocaleString("id-ID");
}

export function parseArmadaCapacityQtyInput(raw) {
  if (raw == null) return { error: "invalid" };
  const s = String(raw).trim();
  if (!s) return { error: "required" };
  if (/[eE]/.test(s) || /infinity|nan/i.test(s)) return { error: "invalid" };
  if (s.includes(",") || s.includes("-") || s.includes("+") || s.includes(" ")) {
    return { error: "invalid" };
  }
  if (s.includes(".")) {
    if (!/^\d{1,3}(\.\d{3})+$/.test(s)) return { error: "invalid" };
  } else if (!/^\d+$/.test(s)) {
    return { error: "invalid" };
  }
  const digits = s.replace(/\./g, "");
  const n = Number(digits);
  if (!Number.isSafeInteger(n) || n <= 0) return { error: "invalid" };
  return { value: n };
}

export function capacitySaveErrorText(err, fallback = "Gagal menyimpan kapasitas.") {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  return fallback;
}
