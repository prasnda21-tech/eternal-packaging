import { formatYmdLocal, parseYmdLocal } from "../components/deliveryPlan/deliveryPlanDateRange";
import { salesJakartaDay } from "./salesPwa";

export const HISTORY_SEARCH_DEBOUNCE_MS = 300;
export const HISTORY_PAGE_SIZE_OPTIONS = [25, 50, 100];

export const EMPTY_HISTORY_FILTERS = {
  date_from: "",
  date_to: "",
  customer_q: "",
  po_q: "",
  sj_q: "",
  item_q: "",
  qty_min: "",
  qty_max: "",
  status: "",
};

/** Inclusive calendar month for Asia/Jakarta: day 1 .. last day of month. */
export function historyDatePresetRange(preset, today = salesJakartaDay()) {
  const day = String(today || "").slice(0, 10);
  const base = parseYmdLocal(day);
  if (!base) return { date_from: "", date_to: "" };
  const ymd = (date) => formatYmdLocal(date);
  if (preset === "today") {
    return { date_from: day, date_to: day };
  }
  if (preset === "7d") {
    const from = new Date(base);
    from.setDate(from.getDate() - 6);
    return { date_from: ymd(from), date_to: day };
  }
  if (preset === "30d") {
    const from = new Date(base);
    from.setDate(from.getDate() - 29);
    return { date_from: ymd(from), date_to: day };
  }
  if (preset === "month") {
    const from = new Date(base.getFullYear(), base.getMonth(), 1);
    const to = new Date(base.getFullYear(), base.getMonth() + 1, 0);
    return { date_from: ymd(from), date_to: ymd(to) };
  }
  return { date_from: "", date_to: "" };
}

/** Default page filters: Bulan Ini (WIB), no text/qty/status limits. */
export function defaultHistoryFilters(today = salesJakartaDay()) {
  const range = historyDatePresetRange("month", today);
  return {
    ...EMPTY_HISTORY_FILTERS,
    date_from: range.date_from,
    date_to: range.date_to,
  };
}

export function isHistoryMonthPreset(filters = {}, today = salesJakartaDay()) {
  const range = historyDatePresetRange("month", today);
  return (
    String(filters.date_from || "").trim() === range.date_from
    && String(filters.date_to || "").trim() === range.date_to
  );
}

export function historyNonDateFiltersActive(filters = {}) {
  return ["customer_q", "po_q", "sj_q", "item_q", "qty_min", "qty_max", "status"]
    .some((key) => String(filters[key] || "").trim() !== "");
}

/** True when Reset should appear: not exactly default Bulan Ini, or extra filters on. */
export function historyFiltersActive(filters = {}, today = salesJakartaDay()) {
  if (historyNonDateFiltersActive(filters)) return true;
  return !isHistoryMonthPreset(filters, today);
}

export function historyActiveDatePreset(filters = {}, today = salesJakartaDay()) {
  const from = String(filters.date_from || "").trim();
  const to = String(filters.date_to || "").trim();
  if (!from && !to) return "";
  const presets = ["today", "7d", "30d", "month"];
  for (const preset of presets) {
    const range = historyDatePresetRange(preset, today);
    if (from === range.date_from && to === range.date_to) return preset;
  }
  return "";
}

export function historyRequestParams(filters = {}, { skip = 0, limit = 25 } = {}) {
  const params = { skip, limit };
  const from = String(filters.date_from || "").trim();
  const to = String(filters.date_to || "").trim();
  if (from) params.date_from = from;
  if (to) params.date_to = to;
  const customer = String(filters.customer_q || "").trim();
  const po = String(filters.po_q || "").trim();
  const sj = String(filters.sj_q || "").trim();
  const item = String(filters.item_q || "").trim();
  const status = String(filters.status || "").trim();
  if (customer) params.customer_q = customer;
  if (po) params.po_q = po;
  if (sj) params.sj_q = sj;
  if (item) params.item_q = item;
  if (status) params.status = status;
  const qtyMin = String(filters.qty_min || "").trim();
  const qtyMax = String(filters.qty_max || "").trim();
  if (qtyMin !== "" && Number.isFinite(Number(qtyMin))) params.qty_min = Number(qtyMin);
  if (qtyMax !== "" && Number.isFinite(Number(qtyMax))) params.qty_max = Number(qtyMax);
  return params;
}

export function formatHistoryQty(qty, unit) {
  if (qty == null || qty === "") return "—";
  const n = Number(qty);
  const amount = Number.isFinite(n) ? n.toLocaleString("id-ID") : String(qty);
  const u = String(unit || "").trim();
  return u ? `${amount} ${u}` : amount;
}

export function formatHistoryDay(isoOrDay) {
  const text = String(isoOrDay || "").trim();
  if (!text) return "—";
  const day = text.length >= 10 && text[4] === "-" ? text.slice(0, 10) : text;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return text;
  return `${m[3]}/${m[2]}/${m[1]}`;
}
