import { NAV_KEYS } from "./navAccess";

export const INVENTORY_VALUE_PATH = "/persediaan/nilai-persediaan";
export const CURRENT_STOCK_PATH = "/persediaan/stock-saat-ini";
export const INVENTORY_VALUE_LINK_LABEL = "Lihat nilai persediaan";
export const CURRENT_STOCK_LINK_LABEL = "Lihat di Stok Saat Ini";
export const BOOK_VALUE_NOTE = "Tidak ditampilkan per barang. Rekonsiliasi hanya menyediakan total.";

const VALUE_TABS = new Set(["daftar", "opening", "hpp", "recon", "ledger-recon"]);

export function stableMasterItemId(value) {
  return String(value ?? "").trim();
}

export function inventoryValueLinkVisible({ masterItemId, itemType, isActive, hasPerm }) {
  if (!stableMasterItemId(masterItemId)) return false;
  if (String(itemType ?? "").trim().toUpperCase() !== "INVENTORY") return false;
  if (isActive === false) return false;
  if (typeof hasPerm !== "function") return false;
  return Boolean(hasPerm(NAV_KEYS.inventoryValue) && hasPerm("inventory_valuation.view"));
}

export function inventoryValueItemPath(masterItemId) {
  const params = new URLSearchParams();
  params.set("master_item_id", stableMasterItemId(masterItemId));
  return `${INVENTORY_VALUE_PATH}?${params.toString()}`;
}

export function inventoryValueReconPath(masterItemId = "") {
  const params = new URLSearchParams();
  params.set("tab", "recon");
  const id = stableMasterItemId(masterItemId);
  if (id) params.set("master_item_id", id);
  return `${INVENTORY_VALUE_PATH}?${params.toString()}`;
}

export function inventoryValueTabFromSearch(search) {
  const raw = String(search || "");
  const params = new URLSearchParams(raw.startsWith("?") ? raw.slice(1) : raw);
  const tab = params.get("tab") || "";
  return VALUE_TABS.has(tab) ? tab : "daftar";
}

export function inventoryValueMasterItemIdFromSearch(search) {
  const raw = String(search || "");
  const params = new URLSearchParams(raw.startsWith("?") ? raw.slice(1) : raw);
  return stableMasterItemId(params.get("master_item_id"));
}

/** Presentation only. Dashboard payloads keep their original href. */
export function reconciliationControlHref(item) {
  if (!item || !item.href || item.status === "error") return null;
  if (item.key !== "inventory_reconciliation") return item.href;
  const [path, query = ""] = String(item.href).split("?");
  const params = new URLSearchParams(query);
  params.set("tab", "ledger-recon");
  const next = params.toString();
  return next ? `${path}?${next}` : path;
}

/** Valuation keeps the existing nav pair. Ledger view is a separate read grant. */
export function inventoryValueRouteAllowed(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  const valuation = Boolean(hasPerm(NAV_KEYS.inventoryValue) && hasPerm("inventory_valuation.view"));
  const ledger = Boolean(hasPerm("inventory_reconciliation.view"));
  return valuation || ledger;
}

export function inventoryEstimateText(item, formatMoney) {
  if (!item || item.inventory_value == null || item.inventory_value === "") return "Belum ada dasar";
  return formatMoney(item.inventory_value);
}

export function acceptValuationFocus(payload, masterItemId) {
  const requested = stableMasterItemId(masterItemId);
  const mismatch = {
    found: false,
    covered: false,
    master_item_id: requested,
    matched_by: null,
    reason_code: "NOT_FOUND",
    reason: "Hasil tidak cocok dengan ID master yang diminta.",
    item: null,
    inventory_value: null,
    book_value_per_item: null,
    writes: 0,
  };
  if (!payload || stableMasterItemId(payload.master_item_id) !== requested) return mismatch;
  if (payload.covered) {
    if (stableMasterItemId(payload.item?.master_item_id) !== requested) return mismatch;
    if (payload.matched_by && payload.matched_by !== "master_item_id") return mismatch;
    return payload;
  }
  return {
    ...payload,
    covered: false,
    item: null,
    inventory_value: null,
    book_value_per_item: null,
  };
}
