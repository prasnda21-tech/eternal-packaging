/** Delivery schedule display helpers. Dates are YYYY-MM-DD keys. */

export function dateKey(raw) {
  if (!raw) return "";
  const text = String(raw).trim();
  if (text.length >= 10 && text[4] === "-" && text[7] === "-") return text.slice(0, 10);
  return "";
}

export function matchesDeliveryScheduleFilters(row, filters = {}) {
  const po = (filters.po || "").trim().toLowerCase();
  if (po && !String(row.order_number || "").toLowerCase().includes(po)) return false;
  const customer = (filters.customer || "").trim().toLowerCase();
  if (customer && !String(row.customer_name || "").toLowerCase().includes(customer)) return false;
  const status = (filters.status || "").trim().toLowerCase();
  if (status && status !== "semua" && row.status !== status) return false;
  const from = dateKey(filters.dateFrom);
  const to = dateKey(filters.dateTo);
  const eff = dateKey(row.effective_delivery_date);
  if (from && (!eff || eff < from)) return false;
  if (to && (!eff || eff > to)) return false;
  return true;
}

export const DELIVERY_SCHEDULE_STATUS_OPTIONS = [
  { key: "semua", label: "Semua status" },
  { key: "belum_dijadwalkan", label: "Belum Dijadwalkan" },
  { key: "terjadwal", label: "Terjadwal" },
  { key: "hari_ini", label: "Hari Ini" },
  { key: "terlambat", label: "Terlambat" },
  { key: "terkirim", label: "Terkirim" },
];

export const EMPTY_DELIVERY_FILTERS = {
  dateFrom: "",
  dateTo: "",
  po: "",
  customer: "",
  status: "semua",
};

export function rescheduleConfirmLabel(canSetDate) {
  return canSetDate ? "Konfirmasi Jadwal Ulang" : "Konfirmasi Permintaan";
}
