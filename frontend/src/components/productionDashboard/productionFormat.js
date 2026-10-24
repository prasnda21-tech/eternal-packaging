/** Presentation-only WIB labels. Does not change stored timestamps. */

const WIB_OFFSET_MS = 7 * 60 * 60 * 1000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];
const UNAVAILABLE = "Waktu pembaruan tidak tersedia";

function parseInstant(value) {
  if (value == null) return null;
  const text = String(value).trim();
  if (!text) return null;
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/.test(text);
  const parsed = new Date(hasZone ? text : `${text}Z`);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed;
}

export function formatProductionUpdatedAt(value) {
  const parsed = parseInstant(value);
  if (!parsed) return "";
  const wib = new Date(parsed.getTime() + WIB_OFFSET_MS);
  const day = String(wib.getUTCDate()).padStart(2, "0");
  const month = MONTHS[wib.getUTCMonth()];
  const year = wib.getUTCFullYear();
  const hour = String(wib.getUTCHours()).padStart(2, "0");
  const minute = String(wib.getUTCMinutes()).padStart(2, "0");
  return `${day} ${month} ${year}, ${hour}.${minute} WIB`;
}

export function productionUpdatedLabel(value) {
  const formatted = formatProductionUpdatedAt(value);
  return formatted ? `Diperbarui ${formatted}` : UNAVAILABLE;
}
