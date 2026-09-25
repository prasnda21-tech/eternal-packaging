/** Presentation helpers for Dashboard Keuangan. No accounting formulas. */

export function jakartaDate(now = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export function periodRange(preset, custom = {}, now = new Date()) {
  const today = jakartaDate(now);
  if (preset === "today") return { start: today, end: today };
  if (preset === "year") return { start: `${today.slice(0, 4)}-01-01`, end: today };
  if (preset === "custom") return { start: custom.start || "", end: custom.end || "" };
  return { start: `${today.slice(0, 8)}01`, end: today };
}

export function shouldApplyDashboardResponse(requestId, latestId) {
  return requestId === latestId;
}

export function formatMoney(amount) {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "—";
  return `Rp ${value.toLocaleString("id-ID", { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

export function formatMetric(status, amount) {
  if (status === "error") return { text: "Gagal muat", kind: "error" };
  if (status === "empty") return { text: "Tidak ada transaksi", kind: "empty" };
  if (status === "unavailable" || amount == null) return { text: "Belum tersedia", kind: "unavailable" };
  return { text: formatMoney(amount), kind: status === "zero" ? "zero" : "value" };
}

export function formatMargin(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${(Number(value) * 100).toLocaleString("id-ID", { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`;
}

export function formatAxisMoney(amount) {
  if (amount == null || amount === "") return "";
  const value = Number(amount);
  if (!Number.isFinite(value)) return "";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  const compact = (n, suffix) => {
    const text = n.toLocaleString("id-ID", { maximumFractionDigits: 1, minimumFractionDigits: 0 });
    return `${sign}${text} ${suffix}`;
  };
  if (abs >= 1_000_000_000) return compact(abs / 1_000_000_000, "M");
  if (abs >= 1_000_000) return compact(abs / 1_000_000, "jt");
  if (abs >= 1_000) return compact(abs / 1_000, "rb");
  return `${sign}${abs.toLocaleString("id-ID", { maximumFractionDigits: 0 })}`;
}

export function chartPlotsSeries(status) {
  return status === "complete" || status === "zero" || status === "incomplete";
}

export function trendShowsDot(pointCount) {
  return pointCount === 1;
}

export function agingChartStatus(receivableStatus, payableStatus) {
  const sides = [receivableStatus, payableStatus];
  if (sides.every((status) => status === "error")) return "error";
  if (sides.every((status) => !status || status === "unavailable")) return "unavailable";
  const plottable = sides.filter((status) => chartPlotsSeries(status));
  if (!plottable.length) return "empty";
  if (plottable.every((status) => status === "zero")) return "zero";
  return "complete";
}

export function groupDetailNotes(entries) {
  const grouped = [];
  const index = new Map();
  (entries || []).forEach((entry) => {
    const text = String(entry?.text || "").replace(/\s+/g, " ").trim();
    const metric = String(entry?.metric || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    if (!index.has(text)) {
      const item = { text, metrics: [] };
      index.set(text, item);
      grouped.push(item);
    }
    const item = index.get(text);
    if (metric && !item.metrics.includes(metric)) item.metrics.push(metric);
  });
  return grouped;
}

export function formatLoadedAt(iso) {
  if (!iso) return "Belum dimuat";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleString("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", day: "numeric", month: "short" });
}
