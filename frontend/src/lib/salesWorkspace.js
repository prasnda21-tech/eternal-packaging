/** Penjualan workspace tabs — same engine as Kas & Bank, sales catalog only. */

import { SALES_NAV_ROUTES, NAV_KEYS } from "./navAccess";
import { createWorkspaceTabApi } from "./workspaceTabs";

export const SALES_WORKSPACE_TABS = [
  { id: "pesanan-penjualan", path: SALES_NAV_ROUTES[NAV_KEYS.salesOrder], label: "Pesanan Penjualan" },
  { id: "pengiriman-pesanan", path: SALES_NAV_ROUTES[NAV_KEYS.salesDeliveryOrder], label: "Pengiriman Pesanan" },
  {
    id: "faktur-penjualan",
    path: SALES_NAV_ROUTES[NAV_KEYS.salesInvoice],
    label: "Faktur Penjualan",
    // Keep list query (tab/filter/page/q) when leaving via workspace chrome.
    rememberSearch: true,
  },
  { id: "penerimaan-penjualan", path: SALES_NAV_ROUTES[NAV_KEYS.salesReceipt], label: "Penerimaan Penjualan" },
  { id: "import-po", path: SALES_NAV_ROUTES[NAV_KEYS.importPo], label: "Import PO" },
  { id: "aktivitas-sales", path: SALES_NAV_ROUTES[NAV_KEYS.salesActivity], label: "Aktivitas Sales", matchPrefix: true },
];

const STORAGE_PREFIX = "ep.sales.workspaceTabs.";

const api = createWorkspaceTabApi({
  catalog: SALES_WORKSPACE_TABS,
  storagePrefix: STORAGE_PREFIX,
});

export function salesTabByPath(pathname) {
  return api.tabByPath(pathname);
}

export function salesWorkspaceStorageKey(user) {
  return api.storageKey(user);
}

export function loadSalesWorkspaceTabs(user) {
  return api.load(user);
}

export function saveSalesWorkspaceTabs(user, tabs) {
  return api.save(user, tabs);
}

/** Clear tabs for one user, or all Penjualan workspace tab keys if user omitted. */
export function clearSalesWorkspaceTabs(user) {
  return api.clear(user);
}

/**
 * Open or activate a tab by path. Same path → no duplicate.
 */
export function openOrActivateSalesTab(tabs, pathname) {
  return api.openOrActivate(tabs, pathname);
}

/**
 * Close a tab. If closing the active one, prefer right neighbor then left.
 */
export function closeSalesTab(tabs, closeId, activePath) {
  return api.closeTab(tabs, closeId, activePath);
}
