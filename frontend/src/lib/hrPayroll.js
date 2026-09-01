export const STATUS_LABELS = {
  DRAFT: "Draft",
  REVIEW: "Review",
  APPROVAL: "Persetujuan",
  FINAL: "Final",
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
