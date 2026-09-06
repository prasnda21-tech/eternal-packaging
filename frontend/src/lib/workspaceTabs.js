/** Shared workspace tab engine (Kas & Bank mechanism). Catalog + sessionStorage only. */

export function normalizeWorkspacePath(pathname) {
  return String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
}

export function createWorkspaceTabApi({ catalog, storagePrefix }) {
  const tabs = Array.isArray(catalog) ? catalog : [];

  function tabByPath(pathname) {
    const path = normalizeWorkspacePath(pathname);
    const exact = tabs.find((t) => t.path === path);
    if (exact) return exact;
    const prefixed = tabs
      .filter((t) => t.matchPrefix && (path === t.path || path.startsWith(`${t.path}/`)))
      .sort((a, b) => b.path.length - a.path.length);
    return prefixed[0] || null;
  }

  function storageKey(user) {
    const id = user?.id || user?.email || user?.sub;
    if (!id) return null;
    return `${storagePrefix}${String(id).toLowerCase()}`;
  }

  function load(user) {
    const key = storageKey(user);
    if (!key || typeof sessionStorage === "undefined") return [];
    try {
      const raw = sessionStorage.getItem(key);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      const allowed = new Set(tabs.map((t) => t.id));
      const byId = Object.fromEntries(tabs.map((t) => [t.id, t]));
      return parsed
        .map((id) => (allowed.has(id) ? byId[id] : null))
        .filter(Boolean);
    } catch {
      return [];
    }
  }

  function save(user, openTabs) {
    const key = storageKey(user);
    if (!key || typeof sessionStorage === "undefined") return;
    try {
      const ids = (openTabs || []).map((t) => t.id);
      sessionStorage.setItem(key, JSON.stringify(ids));
    } catch {
      /* ignore quota / private mode */
    }
  }

  function clear(user) {
    if (typeof sessionStorage === "undefined") return;
    const key = storageKey(user);
    if (key) {
      try { sessionStorage.removeItem(key); } catch { /* ignore */ }
      return;
    }
    try {
      const toRemove = [];
      for (let i = 0; i < sessionStorage.length; i += 1) {
        const k = sessionStorage.key(i);
        if (k && k.startsWith(storagePrefix)) toRemove.push(k);
      }
      toRemove.forEach((k) => sessionStorage.removeItem(k));
    } catch {
      /* ignore */
    }
  }

  function openOrActivate(openTabs, pathname) {
    const entry = tabByPath(pathname);
    if (!entry) return { tabs: openTabs || [], activated: null };
    const list = Array.isArray(openTabs) ? [...openTabs] : [];
    const existing = list.find((t) => t.id === entry.id);
    if (existing) return { tabs: list, activated: existing };
    list.push(entry);
    return { tabs: list, activated: entry };
  }

  function closeTab(openTabs, closeId, activePath) {
    const list = Array.isArray(openTabs) ? [...openTabs] : [];
    const idx = list.findIndex((t) => t.id === closeId);
    if (idx < 0) return { tabs: list, navigateTo: null };
    const closed = list[idx];
    const next = list.filter((t) => t.id !== closeId);
    const activeTab = tabByPath(activePath);
    const closingActive = activeTab?.id === closed.id;
    if (!closingActive) return { tabs: next, navigateTo: null };
    if (!next.length) return { tabs: next, navigateTo: "/" };
    const neighbor = next[Math.min(idx, next.length - 1)];
    return { tabs: next, navigateTo: neighbor.path };
  }

  return {
    catalog: tabs,
    tabByPath,
    storageKey,
    load,
    save,
    clear,
    openOrActivate,
    closeTab,
  };
}
