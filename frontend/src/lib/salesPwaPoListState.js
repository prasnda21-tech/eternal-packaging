export const SALES_PWA_PO_FILTER_KEY = "sales_pwa_po_filter";
export const SALES_PWA_PO_SCROLL_KEY = "sales_pwa_po_scroll";

export const SALES_PWA_PO_STATUSES = ["semua", "berjalan", "hold", "parsial", "selesai"];

export const EMPTY_SALES_PWA_PO_FILTERS = {
  q: "",
  customer_q: "",
  status: "semua",
  stage: "",
  date_from: "",
  date_to: "",
  progress_min: "",
  buffer: "",
};

function text(value) {
  return String(value ?? "");
}

export function normalizeSalesPwaPoFilters(raw) {
  const source = raw && typeof raw === "object" ? raw : {};
  const status = text(source.status);
  const buffer = text(source.buffer);
  return {
    q: text(source.q),
    customer_q: text(source.customer_q),
    status: SALES_PWA_PO_STATUSES.includes(status) ? status : "semua",
    stage: text(source.stage),
    date_from: text(source.date_from),
    date_to: text(source.date_to),
    progress_min: text(source.progress_min),
    buffer: buffer === "ya" || buffer === "tidak" ? buffer : "",
  };
}

export function readSalesPwaPoListState(storage = globalThis.sessionStorage) {
  const empty = { filters: { ...EMPTY_SALES_PWA_PO_FILTERS }, scrollY: 0 };
  if (!storage) return empty;
  try {
    const raw = storage.getItem(SALES_PWA_PO_FILTER_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    const scrollY = Number(storage.getItem(SALES_PWA_PO_SCROLL_KEY));
    return {
      filters: normalizeSalesPwaPoFilters(parsed),
      scrollY: Number.isFinite(scrollY) && scrollY > 0 ? scrollY : 0,
    };
  } catch {
    return empty;
  }
}

export function writeSalesPwaPoListState(filters, scrollY, storage = globalThis.sessionStorage) {
  if (!storage) return;
  const y = Number(scrollY);
  try {
    storage.setItem(SALES_PWA_PO_FILTER_KEY, JSON.stringify(normalizeSalesPwaPoFilters(filters)));
    storage.setItem(SALES_PWA_PO_SCROLL_KEY, String(Number.isFinite(y) && y > 0 ? y : 0));
  } catch {
    /* sessionStorage can reject in private mode. */
  }
}

export function clearSalesPwaPoListState(storage = globalThis.sessionStorage) {
  if (!storage) return;
  try {
    storage.removeItem(SALES_PWA_PO_FILTER_KEY);
    storage.removeItem(SALES_PWA_PO_SCROLL_KEY);
  } catch {
    /* ignore */
  }
}
