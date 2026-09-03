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
