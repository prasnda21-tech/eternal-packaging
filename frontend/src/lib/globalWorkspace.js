/** Global workspace tabs — one catalog from active sidebar leaves. Reuses createWorkspaceTabApi. */

import {
  NAV_KEYS,
  canSeeNav,
  cashBankMenuItems,
  employeeAdminMenuItems,
  hrPayrollMenuItems,
  inventoryMenuItems,
  ledgerMenuItems,
  productionMenuItems,
  purchasingMenuItems,
  reportsMenuItems,
  salesMenuItems,
  settingsMenuItems,
  taskMenuItems,
} from "./navAccess";
import { CASH_BANK_WORKSPACE_TABS } from "./cashBankWorkspace";
import { SALES_WORKSPACE_TABS } from "./salesWorkspace";
import { createWorkspaceTabApi } from "./workspaceTabs";
import { hydratePoDetailStoredTab, poDetailWorkspaceTab } from "./poWorkspaceTab";
import { canSeeSpkNav } from "./spk";

export const GLOBAL_WORKSPACE_STORAGE_PREFIX = "ep.workspaceTabs.";

const PREFIX_PATHS = new Set([
  "/penjualan/aktivitas-sales",
  "/produksi/jadwal-pengiriman",
  "/optimasi-rute",
]);

function tabIdFromPath(path) {
  return String(path || "").replace(/^\//, "").replace(/\//g, "-") || "home";
}

function itemToTab(item, idOverride) {
  const path = item.to;
  return {
    id: idOverride || tabIdFromPath(path),
    path,
    label: item.label,
    ...(PREFIX_PATHS.has(path) ? { matchPrefix: true } : {}),
  };
}

function showRouteOptimizer(hasPerm) {
  return canSeeNav(hasPerm, NAV_KEYS.routeOptimizer)
    || canSeeNav(hasPerm, "route_optimizer.view")
    || canSeeNav(hasPerm, "route_optimizer.create")
    || canSeeNav(hasPerm, "route_optimizer.manage");
}

/** Active sidebar leaf destinations the current user may open as tabs. */
export function visibleGlobalWorkspaceCatalog(hasPerm) {
  const salesIdByPath = Object.fromEntries(SALES_WORKSPACE_TABS.map((t) => [t.path, t.id]));
  const cashBankIdByPath = Object.fromEntries(CASH_BANK_WORKSPACE_TABS.map((t) => [t.path, t.id]));
  const tabs = [];

  if (canSeeNav(hasPerm, NAV_KEYS.dashboard)) {
    tabs.push({ id: "dashboard", path: "/dashboard", label: "Dashboard" });
  }

  if (canSeeNav(hasPerm, NAV_KEYS.chat) || (typeof hasPerm === "function" && hasPerm("chat.use"))) {
    tabs.push({ id: "chat", path: "/chat", label: "Chat", matchPrefix: true });
  }

  if (canSeeSpkNav(hasPerm)) {
    tabs.push({ id: "spk", path: "/spk", label: "SPK", matchPrefix: true });
  }

  productionMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  taskMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  salesMenuItems(hasPerm).forEach((item) => {
    const tab = itemToTab(item, salesIdByPath[item.to]);
    if (tab.path === "/penjualan/permintaan-produk" || tab.path.startsWith("/penjualan/permintaan-produk/")) {
      tabs.push({
        ...tab,
        id: "penjualan-permintaan-produk",
        rememberPath: true,
        matches(path) {
          return path === "/penjualan/permintaan-produk" || path.startsWith("/penjualan/permintaan-produk/");
        },
      });
      return;
    }
    tabs.push(tab);
  });
  purchasingMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  inventoryMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  cashBankMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item, cashBankIdByPath[item.to])));
  ledgerMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  reportsMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  employeeAdminMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));
  hrPayrollMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));

  if (showRouteOptimizer(hasPerm)) {
    tabs.push({
      id: "optimasi-rute",
      path: "/optimasi-rute",
      label: "Optimasi Rute",
      matchPrefix: true,
    });
  }

  settingsMenuItems(hasPerm).forEach((item) => tabs.push(itemToTab(item)));

  if (canSeeNav(hasPerm, NAV_KEYS.settings)) {
    tabs.push({ id: "settings", path: "/settings", label: "Pengaturan" });
  }

  return tabs;
}

/** Full leaf catalog (permission-unfiltered) for path resolution / tests. */
export const GLOBAL_WORKSPACE_TABS = visibleGlobalWorkspaceCatalog(() => true);

export function createGlobalWorkspaceApi(hasPerm) {
  return createWorkspaceTabApi({
    catalog: visibleGlobalWorkspaceCatalog(hasPerm),
    storagePrefix: GLOBAL_WORKSPACE_STORAGE_PREFIX,
    dynamicFromPath: poDetailWorkspaceTab,
    resolveStoredTab: hydratePoDetailStoredTab,
  });
}

const fullApi = createWorkspaceTabApi({
  catalog: GLOBAL_WORKSPACE_TABS,
  storagePrefix: GLOBAL_WORKSPACE_STORAGE_PREFIX,
  dynamicFromPath: poDetailWorkspaceTab,
  resolveStoredTab: hydratePoDetailStoredTab,
});

export function globalTabByPath(pathname) {
  return fullApi.tabByPath(pathname);
}

export function globalWorkspaceStorageKey(user) {
  return fullApi.storageKey(user);
}

export function loadGlobalWorkspaceTabs(user, hasPerm = () => true) {
  return createGlobalWorkspaceApi(hasPerm).load(user);
}

export function saveGlobalWorkspaceTabs(user, tabs) {
  return fullApi.save(user, tabs);
}

export function clearGlobalWorkspaceTabs(user) {
  return fullApi.clear(user);
}

export function openOrActivateGlobalTab(tabs, pathname, hasPerm = () => true, meta) {
  return createGlobalWorkspaceApi(hasPerm).openOrActivate(tabs, pathname, meta);
}

export function closeGlobalTab(tabs, closeId, activePath, hasPerm = () => true) {
  return createGlobalWorkspaceApi(hasPerm).closeTab(tabs, closeId, activePath);
}
