export function formatRupiah(num) {
  const n = Number(num) || 0;
  return "Rp " + n.toLocaleString("id-ID");
}

export function todayYmd() {
  return new Date().toISOString().slice(0, 10);
}

export function newIdempotencyKey() {
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function errDetail(ex) {
  const det = ex?.response?.data?.detail;
  if (typeof det === "object" && det) {
    if (Array.isArray(det.errors) && det.errors.length) {
      return [det.message, ...det.errors].filter(Boolean).join(" · ");
    }
    return det.message || JSON.stringify(det);
  }
  return det || ex?.message || "Terjadi kesalahan";
}

/** Frontend-only payment amount check. Backend remains authoritative. */
export function validatePurchasePaymentAmount(amount, outstanding) {
  const raw = String(amount ?? "").trim();
  if (raw === "") return "Jumlah pembayaran wajib diisi.";
  const n = Number(raw);
  if (!Number.isFinite(n)) return "Jumlah pembayaran tidak valid.";
  if (n <= 0) return "Jumlah pembayaran harus lebih besar dari 0.";
  const out = Number(outstanding);
  if (Number.isFinite(out) && n > out + 1e-9) {
    return `Jumlah pembayaran melebihi sisa tagihan (${formatRupiah(out)}).`;
  }
  return "";
}
