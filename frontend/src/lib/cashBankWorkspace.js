/** Kas & Bank workspace tab catalog + sessionStorage helpers (user-scoped). */

export const CASH_BANK_WORKSPACE_TABS = [
  { id: "daftar", path: "/kas-bank/daftar", label: "Daftar Kas & Bank", rememberSearch: true },
  { id: "penerimaan", path: "/kas-bank/penerimaan", label: "Penerimaan", rememberSearch: true },
  { id: "pembayaran", path: "/kas-bank/pembayaran", label: "Pembayaran", rememberSearch: true },
  { id: "transfer", path: "/kas-bank/transfer", label: "Transfer", rememberSearch: true },
  { id: "mutasi", path: "/kas-bank/mutasi", label: "Mutasi Kas & Bank", rememberSearch: true },
  // Reconciliation stays path-only — actions require latest server data.
  { id: "rekonsiliasi", path: "/kas-bank/rekonsiliasi", label: "Rekonsiliasi Bank" },
  { id: "rekonsiliasi-owner", path: "/kas-bank/rekonsiliasi-owner", label: "Rekonsiliasi Owner" },
];

const STORAGE_PREFIX = "ep.cashBank.workspaceTabs.";

export function cashBankTabByPath(pathname) {
  const path = String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
  return CASH_BANK_WORKSPACE_TABS.find((t) => t.path === path) || null;
}

export function cashBankWorkspaceStorageKey(user) {
  const id = user?.id || user?.email || user?.sub;
  if (!id) return null;
  return `${STORAGE_PREFIX}${String(id).toLowerCase()}`;
}

export function loadCashBankWorkspaceTabs(user) {
  const key = cashBankWorkspaceStorageKey(user);
  if (!key || typeof sessionStorage === "undefined") return [];
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    const allowed = new Set(CASH_BANK_WORKSPACE_TABS.map((t) => t.id));
    const byId = Object.fromEntries(CASH_BANK_WORKSPACE_TABS.map((t) => [t.id, t]));
    return parsed
      .map((id) => (allowed.has(id) ? byId[id] : null))
      .filter(Boolean);
  } catch {
    return [];
  }
}

export function saveCashBankWorkspaceTabs(user, tabs) {
  const key = cashBankWorkspaceStorageKey(user);
  if (!key || typeof sessionStorage === "undefined") return;
  try {
    const ids = (tabs || []).map((t) => t.id);
    sessionStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* ignore quota / private mode */
  }
}

/** Clear tabs for one user, or all Kas & Bank workspace tab keys if user omitted. */
export function clearCashBankWorkspaceTabs(user) {
  if (typeof sessionStorage === "undefined") return;
  const key = cashBankWorkspaceStorageKey(user);
  if (key) {
    try { sessionStorage.removeItem(key); } catch { /* ignore */ }
    return;
  }
  try {
    const toRemove = [];
    for (let i = 0; i < sessionStorage.length; i += 1) {
      const k = sessionStorage.key(i);
      if (k && k.startsWith(STORAGE_PREFIX)) toRemove.push(k);
    }
    toRemove.forEach((k) => sessionStorage.removeItem(k));
  } catch {
    /* ignore */
  }
}

/**
 * Open or activate a tab by path. Same path → no duplicate.
 * @returns {{ tabs: typeof CASH_BANK_WORKSPACE_TABS, activated: object|null }}
 */
export function openOrActivateCashBankTab(tabs, pathname) {
  const catalog = cashBankTabByPath(pathname);
  if (!catalog) return { tabs: tabs || [], activated: null };
  const list = Array.isArray(tabs) ? [...tabs] : [];
  const existing = list.find((t) => t.id === catalog.id);
  if (existing) return { tabs: list, activated: existing };
  list.push(catalog);
  return { tabs: list, activated: catalog };
}

/**
 * Close a tab. If closing the active one, prefer right neighbor then left.
 * @returns {{ tabs: array, navigateTo: string|null }}
 */
export function closeCashBankTab(tabs, closeId, activePath) {
  const list = Array.isArray(tabs) ? [...tabs] : [];
  const idx = list.findIndex((t) => t.id === closeId);
  if (idx < 0) return { tabs: list, navigateTo: null };
  const closed = list[idx];
  const next = list.filter((t) => t.id !== closeId);
  const activeTab = cashBankTabByPath(activePath);
  const closingActive = activeTab?.id === closed.id;
  if (!closingActive) return { tabs: next, navigateTo: null };
  if (!next.length) return { tabs: next, navigateTo: "/" };
  const neighbor = next[Math.min(idx, next.length - 1)];
  return { tabs: next, navigateTo: neighbor.path };
}
