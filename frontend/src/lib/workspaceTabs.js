/** Shared workspace tab engine (Kas & Bank mechanism). Catalog + sessionStorage only. */

export function normalizeWorkspacePath(pathname) {
  return String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
}

export function createWorkspaceTabApi({ catalog, storagePrefix, dynamicFromPath, resolveStoredTab }) {
  const tabs = Array.isArray(catalog) ? catalog : [];

  function tabByPath(pathname, meta) {
    const path = normalizeWorkspacePath(pathname);
    const exact = tabs.find((t) => t.path === path);
    if (exact) return exact;
    const prefixed = tabs
      .filter((t) => t.matchPrefix && (path === t.path || path.startsWith(`${t.path}/`)))
      .sort((a, b) => b.path.length - a.path.length);
    if (prefixed[0]) return prefixed[0];
    if (typeof dynamicFromPath === "function") return dynamicFromPath(pathname, meta) || null;
    return null;
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
        .map((item) => {
          if (typeof item === "string") {
            if (allowed.has(item)) return byId[item];
            return typeof resolveStoredTab === "function" ? resolveStoredTab(item) : null;
          }
          if (item && typeof item === "object") {
            return typeof resolveStoredTab === "function" ? resolveStoredTab(item) : null;
          }
          return null;
        })
        .filter(Boolean);
    } catch {
      return [];
    }
  }

  function save(user, openTabs) {
    const key = storageKey(user);
    if (!key || typeof sessionStorage === "undefined") return;
    try {
      const payload = (openTabs || []).map((t) => (
        t?.dynamic ? { id: t.id, path: t.path, label: t.label } : t.id
      ));
      sessionStorage.setItem(key, JSON.stringify(payload));
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

  function openOrActivate(openTabs, pathname, meta) {
    const entry = tabByPath(pathname, meta);
    if (!entry) return { tabs: openTabs || [], activated: null };
    const list = Array.isArray(openTabs) ? [...openTabs] : [];
    const idx = list.findIndex((t) => t.id === entry.id);
    if (idx >= 0) {
      const existing = list[idx];
      if (entry.dynamic && entry.label && entry.label !== existing.label && meta?.po_number) {
        const updated = { ...existing, label: entry.label, path: entry.path };
        list[idx] = updated;
        return { tabs: list, activated: updated };
      }
      return { tabs: list, activated: existing };
    }
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
