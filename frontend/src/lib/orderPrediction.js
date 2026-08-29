/** Display helpers for Prediksi Order. Values come from the derived API. */

export const FOLLOWUP_STATUSES = [
  { value: "", label: "Semua" },
  { value: "waktu_follow_up", label: "Waktu Follow-up" },
  { value: "jatuh_tempo", label: "Jatuh Tempo" },
  { value: "terlambat", label: "Terlambat" },
  { value: "risiko", label: "Risiko" },
  { value: "tidak_aktif", label: "Tidak Aktif" },
  { value: "data_belum_cukup", label: "Data Belum Cukup" },
];

export const PATTERN_FILTERS = [
  { value: "", label: "Semua" },
  { value: "mingguan", label: "Mingguan" },
  { value: "2_mingguan", label: "2 Mingguan" },
  { value: "bulanan", label: "Bulanan" },
  { value: "2_bulanan", label: "2 Bulanan" },
  { value: "3_bulanan", label: "3 Bulanan" },
  { value: "tidak_rutin", label: "Tidak Rutin" },
];

export const CONFIDENCE_FILTERS = [
  { value: "", label: "Semua" },
  { value: "tinggi", label: "Tinggi" },
  { value: "sedang", label: "Sedang" },
  { value: "rendah", label: "Rendah" },
];

export const EMPTY_PREDIKSI_FILTERS = {
  search: "",
  status: "",
  pattern: "",
  confidence: "",
  sales: "",
};

export function dashValue(value) {
  if (value === 0) return "0";
  if (value == null || value === "") return "-";
  return value;
}

export function dashOmzet(value) {
  if (value == null || value === "" || value === 0) return "-";
  return value;
}

export function dashGap(value) {
  if (value == null || value === "") return "-";
  return value;
}

export function formatStat(value, digits = 1) {
  if (value == null || value === "") return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return Number.isInteger(n) ? String(n) : n.toFixed(digits);
}

export function statusTone(status) {
  switch (status) {
    case "RISIKO":
      return "danger";
    case "TERLAMBAT":
      return "warn";
    case "JATUH TEMPO":
      return "urgent";
    case "WAKTU FOLLOW-UP":
      return "success";
    case "TIDAK AKTIF":
      return "default";
    default:
      return "default";
  }
}

export function kpiFilterForCard(card) {
  if (card === "waktu_follow_up") return "waktu_follow_up";
  if (card === "jatuh_tempo") return "jatuh_tempo";
  if (card === "terlambat_risiko") return "terlambat_risiko";
  if (card === "tidak_aktif") return "tidak_aktif";
  return "";
}
