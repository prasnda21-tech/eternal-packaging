const MONTHS_ID = [
  "Jan", "Feb", "Mar", "Apr", "Mei", "Jun",
  "Jul", "Agu", "Sep", "Okt", "Nov", "Des",
];

/** Local calendar date only — never UTC parse that can shift the day. */
export function parseYmdLocal(ymd) {
  if (!ymd || typeof ymd !== "string") return undefined;
  const m = ymd.slice(0, 10).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return undefined;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  const d = Number(m[3]);
  if (!y || mo < 1 || mo > 12 || d < 1 || d > 31) return undefined;
  return new Date(y, mo - 1, d);
}

export function formatYmdLocal(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function formatDdMmmYyyy(ymd) {
  const date = parseYmdLocal(ymd);
  if (!date) return "";
  return `${String(date.getDate()).padStart(2, "0")} ${MONTHS_ID[date.getMonth()]} ${date.getFullYear()}`;
}

export function formatRangeLabel(dateFrom, dateTo) {
  if (!dateFrom && !dateTo) return "Semua tanggal";
  if (dateFrom && dateTo) return `${formatDdMmmYyyy(dateFrom)} – ${formatDdMmmYyyy(dateTo)}`;
  if (dateFrom) return `Dari ${formatDdMmmYyyy(dateFrom)}`;
  return `Sampai ${formatDdMmmYyyy(dateTo)}`;
}

export function normalizeRangeYmd(fromYmd, toYmd) {
  if (!fromYmd || !toYmd) return { from: fromYmd || "", to: toYmd || "" };
  if (fromYmd <= toYmd) return { from: fromYmd, to: toYmd };
  return { from: toYmd, to: fromYmd };
}
