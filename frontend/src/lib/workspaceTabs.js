/** Shared workspace tab engine (Kas & Bank mechanism). Catalog + sessionStorage only. */

export function normalizeWorkspacePath(pathname) {
  return String(pathname || "").split("?")[0].replace(/\/$/, "") || "/";
}

/** Normalize `?a=1` / `a=1` / empty for opt-in rememberSearch tabs. */
export function normalizeWorkspaceSearch(search) {
  const raw = String(search ?? "").trim();
  if (!raw || raw === "?") return "";
  return raw.startsWith("?") ? raw : `?${raw}`;
}

/** Href used when activating/closing onto a tab (path + optional remembered search). */
export function workspaceTabHref(tab) {
  const path = normalizeWorkspacePath(tab?.path);
  if (!tab?.rememberSearch) return path;
  const search = normalizeWorkspaceSearch(tab.search);
  return search ? `${path}${search}` : path;
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
    const custom = tabs.find((t) => typeof t.matches === "function" && t.matches(path));
    if (custom) return custom;
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
            const base = item.id ? byId[item.id] : null;
            const remembered = base?.rememberPath && typeof item.path === "string"
              ? normalizeWorkspacePath(item.path)
              : "";
            if (base && remembered && typeof base.matches === "function" && base.matches(remembered)) {
              const row = { ...base, path: remembered };
              if (base.rememberSearch) {
                const search = normalizeWorkspaceSearch(item.search);
                if (search) row.search = search;
              }
              return row;
            }
            if (base?.rememberSearch) {
              const search = normalizeWorkspaceSearch(item.search);
              return search ? { ...base, search } : { ...base };
            }
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
      const payload = (openTabs || []).map((t) => {
        if (t?.rememberPath && t.path) {
          const row = { id: t.id, path: t.path, rememberPath: true };
          if (t.rememberSearch) {
            row.rememberSearch = true;
            const search = normalizeWorkspaceSearch(t.search);
            if (search) row.search = search;
          }
          return row;
        }
        if (t?.rememberSearch) {
          const row = { id: t.id, rememberSearch: true };
          const search = normalizeWorkspaceSearch(t.search);
          if (search) row.search = search;
          return row;
        }
        if (!t?.dynamic) return t.id;
        const row = { id: t.id, path: t.path, label: t.label };
        if (t.title) row.title = t.title;
        if (Number.isFinite(t.labelPriority)) row.labelPriority = t.labelPriority;
        return row;
      });
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

  /**
   * @param {unknown} search When undefined, do not change a rememberSearch tab's
   *   stored query (enricher / callers that only pass pathname). When a string
   *   (including ""), sync stored search to the current location.
   */
  function openOrActivate(openTabs, pathname, meta, search) {
    const entry = tabByPath(pathname, meta);
    if (!entry) return { tabs: openTabs || [], activated: null };
    const path = normalizeWorkspacePath(pathname);
    const rememberPath = Boolean(entry.rememberPath)
      && typeof entry.matches === "function"
      && entry.matches(path);
    const searchProvided = search !== undefined;
    const pathQuery = String(pathname || "").includes("?")
      ? normalizeWorkspaceSearch(String(pathname).slice(String(pathname).indexOf("?")))
      : "";
    const nextSearch = searchProvided
      ? normalizeWorkspaceSearch(search)
      : (entry.rememberSearch && pathQuery ? pathQuery : null);
    const applySearch = entry.rememberSearch && nextSearch !== null;

    let tracked = rememberPath ? { ...entry, path } : { ...entry };
    if (applySearch) {
      if (nextSearch) tracked = { ...tracked, search: nextSearch };
      else {
        const { search: _drop, ...rest } = tracked;
        tracked = rest;
      }
    }

    const list = Array.isArray(openTabs) ? [...openTabs] : [];
    const idx = list.findIndex((t) => t.id === entry.id);
    if (idx >= 0) {
      const existing = list[idx];
      let updated = existing;
      let changed = false;

      if (rememberPath && existing.path !== path) {
        updated = { ...updated, path };
        changed = true;
      }
      if (applySearch && normalizeWorkspaceSearch(existing.search) !== nextSearch) {
        if (nextSearch) updated = { ...updated, search: nextSearch, rememberSearch: true };
        else {
          const { search: _drop, ...rest } = updated;
          updated = { ...rest, rememberSearch: true };
        }
        changed = true;
      }

      if (changed) {
        list[idx] = updated;
        return { tabs: list, activated: updated };
      }

      // Dynamic tabs: upgrade label only when navigation meta carries richer
      // info. Null/empty meta (tab click, refresh, back/forward) must keep the
      // existing customer label — never downgrade to PO <order_id>.
      if (entry.dynamic && entry.label && entry.label !== existing.label) {
        const info = meta && typeof meta === "object" ? meta : null;
        const hasCustomer = Boolean(String(info?.customer || "").trim());
        const hasPoNumber = Boolean(String(info?.po_number || "").trim());
        const newPri = Number.isFinite(entry.labelPriority) ? entry.labelPriority : 0;
        let oldPri = Number.isFinite(existing.labelPriority) ? existing.labelPriority : null;
        if (oldPri == null) {
          const text = String(existing.label || "").trim();
          if (!text) oldPri = 0;
          else if (/^PO(\s|$)/i.test(text)) oldPri = 1;
          else oldPri = 2; // non-PO label = treat as customer-tier; never downgrade
        }
        const canUpgrade = Boolean(info) && (
          (hasCustomer && newPri >= oldPri)
          || (hasPoNumber && !hasCustomer && newPri > oldPri)
        );
        if (canUpgrade) {
          const upgraded = {
            ...existing,
            label: entry.label,
            path: entry.path,
            ...(entry.title ? { title: entry.title } : {}),
            labelPriority: newPri,
          };
          list[idx] = upgraded;
          return { tabs: list, activated: upgraded };
        }
      }
      return { tabs: list, activated: existing };
    }
    list.push(tracked);
    return { tabs: list, activated: tracked };
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
    return { tabs: next, navigateTo: workspaceTabHref(neighbor) };
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
