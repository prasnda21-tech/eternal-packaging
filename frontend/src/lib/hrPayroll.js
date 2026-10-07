export const STATUS_LABELS = {
  DRAFT: "Draft",
  REVIEW: "Review",
  APPROVAL: "Persetujuan",
  FINAL: "Final",
};

export const PAYMENT_VISIBILITY_LABELS = {
  BELUM_DIBAYAR: "Belum Dibayar",
  MENUNGGU_PERSETUJUAN: "Menunggu Persetujuan",
  SIAP_DIPOSTING: "Siap Diposting",
  SUDAH_DIBAYAR: "Sudah Dibayar",
};

export const ALLOWED_TRANSITIONS = {
  DRAFT: ["REVIEW"],
  REVIEW: ["APPROVAL", "DRAFT"],
  APPROVAL: ["FINAL", "REVIEW"],
  FINAL: [],
};

export function statusLabel(status) {
  const key = String(status || "").toUpperCase();
  return STATUS_LABELS[key] || status || "-";
}

/** Map active payment.status (or period.payment_status) → visibility code. */
export function paymentVisibilityCode(paymentOrCode, period) {
  if (period && String(period.status || "").toUpperCase() !== "FINAL") return null;
  if (paymentOrCode && typeof paymentOrCode === "object") {
    const existing = String(paymentOrCode.payment_status || "").toUpperCase();
    if (PAYMENT_VISIBILITY_LABELS[existing]) return existing;
    const raw = String(paymentOrCode.status || "").toUpperCase();
    if (raw === "DRAFT") return "MENUNGGU_PERSETUJUAN";
    if (raw === "APPROVED") return "SIAP_DIPOSTING";
    if (raw === "PAID") return "SUDAH_DIBAYAR";
    // Cancelled / reversed / unknown: only "Belum Dibayar" when FINAL period has no active payment.
    if (period && String(period.status || "").toUpperCase() === "FINAL") return "BELUM_DIBAYAR";
    return null;
  }
  const code = String(paymentOrCode || "").toUpperCase();
  if (PAYMENT_VISIBILITY_LABELS[code]) return code;
  if (!period || String(period.status || "").toUpperCase() !== "FINAL") return null;
  return "BELUM_DIBAYAR";
}

export function paymentVisibilityLabel(paymentOrCode, period) {
  const code = paymentVisibilityCode(paymentOrCode, period);
  return code ? (PAYMENT_VISIBILITY_LABELS[code] || code) : "";
}

export function moneyFormat(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "Rp0";
  return new Intl.NumberFormat("id-ID", {
    style: "currency",
    currency: "IDR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  }).format(amount);
}

export function canTransition(from, to) {
  const current = String(from || "").toUpperCase();
  const target = String(to || "").toUpperCase();
  return (ALLOWED_TRANSITIONS[current] || []).includes(target);
}

export function nextStatuses(status) {
  return [...(ALLOWED_TRANSITIONS[String(status || "").toUpperCase()] || [])];
}
